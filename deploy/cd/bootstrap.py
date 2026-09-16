"""One-time IAM/OIDC/S3 setup; IAM operator credentials are saved locally, never printed."""
import argparse
import configparser
import json
import os
from pathlib import Path
import tempfile

import boto3
from botocore.exceptions import ClientError

from deploy.cd.model_artifact import MANIFEST, ROOT, pack


def policies(config):
    bucket = f"arn:aws:s3:::{config['bucket']}"
    send = {"Effect": "Allow", "Action": "ssm:SendCommand", "Resource": [
        f"arn:aws:ec2:{config['region']}:{config['account_id']}:instance/{config['instance_id']}",
        f"arn:aws:ssm:{config['region']}::document/AWS-RunShellScript"]}
    poll = {"Effect": "Allow", "Action": "ssm:GetCommandInvocation", "Resource": "*"}
    workflow = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "s3:GetObject", "Resource": bucket + "/models/*"},
        {"Effect": "Allow", "Action": ["s3:PutObject", "s3:AbortMultipartUpload"], "Resource": bucket + "/releases/*"},
        send, poll,
    ]}
    instance = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "s3:GetObject", "Resource": bucket + "/releases/*"}]}
    operator = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"],
         "Resource": [bucket + "/models/*", bucket + "/releases/*"]},
        send, poll,
        {"Effect": "Allow", "Action": ["ec2:DescribeInstances", "ssm:DescribeInstanceInformation"], "Resource": "*"},
    ]}
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "sts:AssumeRoleWithWebIdentity",
        "Principal": {"Federated": f"arn:aws:iam::{config['account_id']}:oidc-provider/token.actions.githubusercontent.com"},
        "Condition": {"StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                                       "token.actions.githubusercontent.com:sub": config["oidc_subject"]}}}]}
    return workflow, instance, operator, trust


def write_operator_profile(iam, config):
    profile_name = "usedcar-deploy"
    path = Path.home() / ".aws/credentials"
    credentials = configparser.RawConfigParser()
    credentials.read(path)
    if credentials.has_section(profile_name):
        identity = boto3.Session(profile_name=profile_name, region_name=config["region"]).client("sts").get_caller_identity()
        if identity["Arn"] != f"arn:aws:iam::{config['account_id']}:user/{config['operator_user']}":
            raise RuntimeError("Existing usedcar-deploy profile belongs to another identity")
        return
    if iam.list_access_keys(UserName=config["operator_user"])["AccessKeyMetadata"]:
        raise RuntimeError("Operator already has a key but no local profile; do not create duplicate credentials")
    key = iam.create_access_key(UserName=config["operator_user"])["AccessKey"]
    temporary = None
    try:
        credentials.add_section(profile_name)
        credentials.set(profile_name, "aws_access_key_id", key["AccessKeyId"])
        credentials.set(profile_name, "aws_secret_access_key", key["SecretAccessKey"])
        credentials.set(profile_name, "region", config["region"])
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".usedcar-credentials-", dir=path.parent)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as handle:
            credentials.write(handle)
        os.replace(temporary, path)
    except BaseException:
        iam.delete_access_key(UserName=config["operator_user"], AccessKeyId=key["AccessKeyId"])
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()
    config = json.loads((ROOT / "deploy/cd/config.json").read_text())
    data, manifest = pack()
    # A model update must first be made explicit in the versioned manifest.
    if json.loads(MANIFEST.read_text()) != manifest:
        raise RuntimeError("Local model differs from manifest; run model_artifact pack and review the update first")
    session = boto3.Session(profile_name=args.profile, region_name=config["region"])
    if session.client("sts").get_caller_identity()["Account"] != config["account_id"]:
        raise RuntimeError("Wrong AWS account")
    iam, s3 = session.client("iam"), session.client("s3")
    workflow_policy, instance_policy, operator_policy, trust = policies(config)
    provider = trust["Statement"][0]["Principal"]["Federated"]
    try:
        info = iam.get_open_id_connect_provider(OpenIDConnectProviderArn=provider)
        if "sts.amazonaws.com" not in info["ClientIDList"]:
            iam.add_client_id_to_open_id_connect_provider(OpenIDConnectProviderArn=provider, ClientID="sts.amazonaws.com")
    except iam.exceptions.NoSuchEntityException:
        iam.create_open_id_connect_provider(Url="https://token.actions.githubusercontent.com",
            ClientIDList=["sts.amazonaws.com"], Tags=[{"Key": "Project", "Value": "usedcar"}])
    try:
        role = iam.get_role(RoleName=config["github_role"])["Role"]
        if {item["Key"]: item["Value"] for item in role.get("Tags", [])}.get("Project") != "usedcar":
            raise RuntimeError("Existing GitHub role is not managed by this project")
        iam.update_assume_role_policy(RoleName=config["github_role"], PolicyDocument=json.dumps(trust))
    except iam.exceptions.NoSuchEntityException:
        iam.create_role(RoleName=config["github_role"], AssumeRolePolicyDocument=json.dumps(trust),
                        Description="GitHub main branch deployment to the usedcar EC2 only",
                        Tags=[{"Key": "Project", "Value": "usedcar"}])
    iam.put_role_policy(RoleName=config["github_role"], PolicyName="usedcar-cd", PolicyDocument=json.dumps(workflow_policy))
    iam.put_role_policy(RoleName=config["instance_role"], PolicyName="usedcar-cd-read", PolicyDocument=json.dumps(instance_policy))

    bucket = config["bucket"]
    try:
        s3.head_bucket(Bucket=bucket, ExpectedBucketOwner=config["account_id"])
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "404":
            raise
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": config["region"]})
    s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={key: True for key in (
        "BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")})
    s3.put_bucket_ownership_controls(Bucket=bucket, OwnershipControls={"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]})
    s3.put_bucket_encryption(Bucket=bucket, ServerSideEncryptionConfiguration={"Rules": [
        {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]})
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps({"Version": "2012-10-17", "Statement": [{
        "Sid": "RequireTLS", "Effect": "Deny", "Principal": "*", "Action": "s3:*",
        "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
        "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]}))
    s3.put_bucket_lifecycle_configuration(Bucket=bucket, LifecycleConfiguration={"Rules": [{
        "ID": "ExpireTransferredImages", "Status": "Enabled", "Filter": {"Prefix": "releases/"},
        "Expiration": {"Days": 7}, "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1}}]})
    s3.put_object(Bucket=bucket, Key=manifest["key"], Body=data, ContentType="application/gzip")
    try:
        user = iam.get_user(UserName=config["operator_user"])["User"]
        if {item["Key"]: item["Value"] for item in user.get("Tags", [])}.get("Project") != "usedcar":
            raise RuntimeError("Existing operator user is not managed by this project")
    except iam.exceptions.NoSuchEntityException:
        iam.create_user(UserName=config["operator_user"], Tags=[{"Key": "Project", "Value": "usedcar"}])
    iam.put_user_policy(UserName=config["operator_user"], PolicyName="usedcar-deploy-operator", PolicyDocument=json.dumps(operator_policy))
    write_operator_profile(iam, config)
    print(json.dumps({"github_role": config["github_role"], "operator_user": config["operator_user"],
                      "local_profile": "usedcar-deploy", "bucket": bucket, "model_sha256": manifest["sha256"]}))


if __name__ == "__main__":
    main()
