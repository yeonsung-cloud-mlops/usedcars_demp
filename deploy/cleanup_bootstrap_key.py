"""Delete the root bootstrap access key and only its matching local references.

Explicit final-step command, separate from provisioning/deploying. Never logs key values.
"""
import argparse
import configparser
import json
import os
from pathlib import Path

import boto3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--matching-csv", type=Path)
    args = parser.parse_args()
    state_path = Path(__file__).resolve().parent / ".local/aws-state.json"
    state = json.loads(state_path.read_text())
    if not state.get("verified") or not state.get("staging_cleaned"):
        raise RuntimeError("Verify deployment and clean staging before removing the bootstrap key")
    session = boto3.Session(profile_name=args.profile, region_name=state["region"])
    identity = session.client("sts").get_caller_identity()
    if identity["Account"] != state["account"] or identity["Arn"] != f"arn:aws:iam::{state['account']}:root":
        raise RuntimeError("Expected the explicitly authorized root bootstrap profile")
    credential = session.get_credentials().get_frozen_credentials()
    if credential.token:
        raise RuntimeError("Temporary STS credentials are not an IAM access key to delete")
    credential_file = Path.home() / ".aws/credentials"
    config = configparser.RawConfigParser()
    config.read(credential_file)
    matching = [section for section in config.sections()
                if config.get(section, "aws_access_key_id", fallback="") == credential.access_key]
    if args.profile not in matching:
        raise RuntimeError("Expected bootstrap key in the selected local credential profile")
    csv_matches = bool(args.matching_csv and args.matching_csv.exists()
                       and credential.access_key in args.matching_csv.read_text())
    iam = session.client("iam")
    iam.delete_access_key(AccessKeyId=credential.access_key)
    state["bootstrap_key_deleted"] = True
    state_path.write_text(json.dumps(state, indent=2) + "\n")
    for section in matching:
        config.remove_section(section)
    # Write no backup containing the removed key. Existing unrelated profiles are retained.
    temporary = credential_file.with_name(".credentials.usedcar-cleanup")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        config.write(out)
    os.replace(temporary, credential_file)
    config_file = Path.home() / ".aws/config"
    if config_file.exists():
        settings = configparser.RawConfigParser()
        settings.read(config_file)
        changed = False
        for section in matching:
            changed |= settings.remove_section("profile " + section if section != "default" else "default")
        if changed:
            with config_file.open("w") as out:
                settings.write(out)
    if csv_matches:
        args.matching_csv.unlink()
    state["bootstrap_local_profiles_removed"] = matching
    state["bootstrap_csv_removed"] = csv_matches
    state_path.write_text(json.dumps(state, indent=2) + "\n")
    print(json.dumps({"aws_access_key_deleted": True, "local_profiles_removed": matching,
                      "matching_csv_deleted": csv_matches, "key_values_logged": False}))


if __name__ == "__main__":
    main()
