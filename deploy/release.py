"""Package this app, deploy through SSM, inspect status, and remove staging resources."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tarfile
import uuid

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "deploy/.local/aws-state.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["status", "associate", "deploy", "cleanup-staging"])
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()
    state = json.loads(STATE.read_text())
    session = boto3.Session(profile_name=args.profile, region_name=state["region"])
    if session.client("sts").get_caller_identity()["Account"] != state["account"]:
        raise RuntimeError("AWS account does not match deployment state")
    ec2, ssm, s3, iam = [session.client(service) for service in ("ec2", "ssm", "s3", "iam")]

    def save(**values):
        state.update(values)
        STATE.write_text(json.dumps(state, indent=2) + "\n")

    if args.action == "associate":
        response = ec2.associate_address(InstanceId=state["instance_id"], AllocationId=state["allocation_id"])
        save(association_id=response["AssociationId"])
        print("Elastic IP associated:", state["public_ip"])
    elif args.action == "status":
        instance = ec2.describe_instances(InstanceIds=[state["instance_id"]])["Reservations"][0]["Instances"][0]
        online = ssm.describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": [state["instance_id"]]}])["InstanceInformationList"]
        print(json.dumps({"instance_state": instance["State"]["Name"], "ip": instance.get("PublicIpAddress"),
                          "ssm": [{"status": item["PingStatus"], "platform": item.get("PlatformName")} for item in online]}))
        if state.get("command_id"):
            try:
                result = ssm.get_command_invocation(CommandId=state["command_id"], InstanceId=state["instance_id"])
                print(json.dumps({key: result[key] for key in ("Status", "StandardOutputContent", "StandardErrorContent")}, indent=2))
            except ssm.exceptions.InvocationDoesNotExist:
                print("SSM command pending")
    elif args.action == "deploy":
        if not (ROOT / "frontend/out/index.html").exists():
            raise RuntimeError("Run npm ci and npm run build in frontend first")
        if "staging_bucket" not in state:
            save(staging_bucket=f"usedcar-deploy-{state['account']}-{uuid.uuid4().hex[:8]}")
        bucket = state["staging_bucket"]
        try:
            s3.head_bucket(Bucket=bucket)
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
                raise
            s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": state["region"]})
        s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={key: True for key in (
            "BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")})
        s3.put_bucket_encryption(Bucket=bucket, ServerSideEncryptionConfiguration={"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]})
        release = uuid.uuid4().hex[:12]
        archive = ROOT / f"deploy/.local/release-{release}.tgz"
        included = ["requirements.txt", "price_model.py", "artifacts/price_model.joblib",
                    "backend/requirements.txt", "backend/Dockerfile", "backend/app/__init__.py", "backend/app/main.py",
                    "deploy/compose.yaml", "deploy/Dockerfile.web", "deploy/nginx.conf", ".dockerignore"]
        with tarfile.open(archive, "w:gz") as tar:
            for name in included:
                tar.add(ROOT / name, arcname=name, recursive=False)
            tar.add(ROOT / "frontend/out", arcname="frontend/out")
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        key = f"releases/{release}.tgz"
        s3.upload_file(str(archive), bucket, key)
        iam.put_role_policy(RoleName=state["role_name"], PolicyName="usedcar-bootstrap-artifacts", PolicyDocument=json.dumps({
            "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{bucket}/{key}"}]}))
        commands = [
            "set -eu",
            "command -v docker >/dev/null && docker compose version >/dev/null",
            f"install -d -m 755 /opt/usedcar/releases/{release}",
            f"python3 -c \"import boto3; boto3.client('s3', region_name='{state['region']}').download_file('{bucket}', '{key}', '/opt/usedcar/releases/{release}/bundle.tgz')\"",
            f"cd /opt/usedcar/releases/{release}",
            f"echo '{sha}  bundle.tgz' | sha256sum -c -",
            "tar -xzf bundle.tgz",
            f"printf 'RELEASE={release}\\n' > deploy/.env",
            "docker compose -f deploy/compose.yaml --env-file deploy/.env build",
            "docker compose -f deploy/compose.yaml --env-file deploy/.env up -d --wait --wait-timeout 180",
            "curl --fail --silent http://127.0.0.1/api/health/ready",
            "if [ -L /opt/usedcar/current ]; then readlink /opt/usedcar/current > /opt/usedcar/previous-release; fi",
            f"ln -sfn /opt/usedcar/releases/{release} /opt/usedcar/current",
            "docker compose -f deploy/compose.yaml --env-file deploy/.env ps",
        ]
        response = ssm.send_command(InstanceIds=[state["instance_id"]], DocumentName="AWS-RunShellScript",
                                   Parameters={"commands": commands, "executionTimeout": ["900"]},
                                   TimeoutSeconds=900, Comment="Deploy usedcar application and validate readiness")
        save(release=release, release_sha256=sha, command_id=response["Command"]["CommandId"])
        print(json.dumps({"release": release, "command_id": state["command_id"], "archive_bytes": archive.stat().st_size}))
    else:
        try:
            iam.delete_role_policy(RoleName=state["role_name"], PolicyName="usedcar-bootstrap-artifacts")
        except iam.exceptions.NoSuchEntityException:
            pass
        bucket = state.get("staging_bucket")
        if bucket:
            # Only delete the explicit bucket recorded for this deployment.
            if not re.fullmatch(r"usedcar-deploy-" + state["account"] + r"-[a-f0-9]{8}", bucket):
                raise RuntimeError("Unexpected staging bucket name")
            try:
                listing = s3.list_objects_v2(Bucket=bucket)
                if listing.get("IsTruncated"):
                    raise RuntimeError("Unexpectedly large staging bucket")
                if listing.get("Contents"):
                    s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": obj["Key"]} for obj in listing["Contents"]]})
                s3.delete_bucket(Bucket=bucket)
            except s3.exceptions.NoSuchBucket:
                pass
        save(staging_cleaned=True)
        print("Temporary deployment bucket and instance S3 permissions removed")


if __name__ == "__main__":
    main()
