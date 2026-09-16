"""Provision one EC2 and its SSM instance role; credentials stay in AWS profiles.

Usage: python3 deploy/provision.py --profile temp
State is recorded after each mutation so an interrupted run can safely resume.
"""
import argparse
import json
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".local" / "aws-state.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", default="ap-northeast-2")
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    ec2, iam = session.client("ec2"), session.client("iam")
    account = session.client("sts").get_caller_identity()["Account"]
    state = json.loads(STATE.read_text()) if STATE.exists() else {"account": account, "region": args.region}
    if state["account"] != account or state["region"] != args.region:
        raise RuntimeError("Existing deployment state belongs to a different account/region")

    def save(**values):
        state.update(values)
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, indent=2) + "\n")

    role_name = "usedcar-service-ec2"
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        if role["AssumeRolePolicyDocument"] != trust:
            raise RuntimeError("Existing role trust differs; refusing to modify an unrelated role")
    except iam.exceptions.NoSuchEntityException:
        role = iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=json.dumps(trust),
                               Description="Used car EC2 management through SSM; no model application AWS credentials",
                               Tags=[{"Key": "Project", "Value": "usedcar"}])["Role"]
    save(role_name=role_name, role_arn=role["Arn"])
    iam.attach_role_policy(RoleName=role_name, PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
    try:
        profile = iam.get_instance_profile(InstanceProfileName=role_name)["InstanceProfile"]
    except iam.exceptions.NoSuchEntityException:
        profile = iam.create_instance_profile(InstanceProfileName=role_name)["InstanceProfile"]
    if not profile["Roles"]:
        iam.add_role_to_instance_profile(InstanceProfileName=role_name, RoleName=role_name)
    elif [r["RoleName"] for r in profile["Roles"]] != [role_name]:
        raise RuntimeError("Unexpected instance profile role")
    save(instance_profile=role_name)

    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if len(vpcs) != 1:
        raise RuntimeError("Expected one default VPC; provide a deliberate network plan")
    vpc = vpcs[0]["VpcId"]
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc]},
                                           {"Name": "default-for-az", "Values": ["true"]}])["Subnets"]
    subnet = sorted(subnets, key=lambda x: x["AvailabilityZone"])[0]
    save(vpc_id=vpc, subnet_id=subnet["SubnetId"])
    if "security_group_id" not in state:
        groups = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": ["usedcar-web"]}, {"Name": "vpc-id", "Values": [vpc]}])["SecurityGroups"]
        if groups:
            raise RuntimeError("A usedcar-web security group already exists outside this deployment state")
        group = ec2.create_security_group(GroupName="usedcar-web", Description="Used car public HTTP only; administration through SSM", VpcId=vpc,
                    TagSpecifications=[{"ResourceType": "security-group", "Tags": [{"Key": "Project", "Value": "usedcar"}]}])
        save(security_group_id=group["GroupId"])
    try:
        ec2.authorize_security_group_ingress(GroupId=state["security_group_id"], IpPermissions=[
            {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "Public web application"}]}])
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise
    images = ec2.describe_images(Owners=["099720109477"], Filters=[
        {"Name": "name", "Values": ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]},
        {"Name": "state", "Values": ["available"]}])["Images"]
    ami = max(images, key=lambda image: image["CreationDate"])
    user_data = """#!/bin/bash
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io docker-compose-v2 python3-boto3
systemctl enable --now docker
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
if ! snap list amazon-ssm-agent >/dev/null 2>&1; then snap install amazon-ssm-agent --classic; fi
snap start amazon-ssm-agent
install -d -m 755 /opt/usedcar/releases
"""
    if "instance_id" not in state:
        response = ec2.run_instances(ImageId=ami["ImageId"], InstanceType="t3.small", MinCount=1, MaxCount=1,
            ClientToken="usedcar-web-20260916", SubnetId=state["subnet_id"], SecurityGroupIds=[state["security_group_id"]],
            IamInstanceProfile={"Name": role_name}, UserData=user_data,
            MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled", "HttpPutResponseHopLimit": 1},
            CreditSpecification={"CpuCredits": "standard"},
            BlockDeviceMappings=[{"DeviceName": ami["RootDeviceName"], "Ebs": {"VolumeSize": 20, "VolumeType": "gp3", "Encrypted": True, "DeleteOnTermination": True}}],
            TagSpecifications=[{"ResourceType": kind, "Tags": [{"Key": "Name", "Value": "usedcar-web"}, {"Key": "Project", "Value": "usedcar"}]} for kind in ("instance", "volume")])
        save(instance_id=response["Instances"][0]["InstanceId"], ami_id=ami["ImageId"])
    if "allocation_id" not in state:
        address = ec2.allocate_address(Domain="vpc", TagSpecifications=[{"ResourceType": "elastic-ip", "Tags": [{"Key": "Project", "Value": "usedcar"}]}])
        save(allocation_id=address["AllocationId"], public_ip=address["PublicIp"])
    print(json.dumps(state, indent=2))
    print("Next: wait for instance running, then associate the saved Elastic IP and verify SSM.")


if __name__ == "__main__":
    main()
