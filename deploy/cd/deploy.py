"""Upload locally built immutable images, invoke SSM and fail on deployment errors."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import time

import boto3

ROOT = Path(__file__).resolve().parents[2]
RELEASE_PATTERN = re.compile(r"[a-f0-9]{40}-[0-9]+-[0-9]+")


def validate_release(value):
    if not RELEASE_PATTERN.fullmatch(value):
        raise ValueError("Release must be commit SHA, run ID and run attempt")
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True, type=validate_release)
    args = parser.parse_args()
    config = json.loads((ROOT / "deploy/cd/config.json").read_text())
    manifest = json.loads((ROOT / "deploy/cd/model-manifest.json").read_text())
    session = boto3.Session(region_name=config["region"])
    if session.client("sts").get_caller_identity()["Account"] != config["account_id"]:
        raise RuntimeError("Wrong AWS account")
    work = ROOT / "deploy/.local" / args.release
    work.mkdir(parents=True, exist_ok=False)
    images = [f"usedcar-{name}:{args.release}" for name in ("api", "web")]
    with (work / "images.tar.gz").open("wb") as out:
        with subprocess.Popen(["docker", "save", *images], stdout=subprocess.PIPE) as process:
            with gzip.GzipFile(fileobj=out, mode="wb", compresslevel=1, mtime=0) as compressed:
                shutil.copyfileobj(process.stdout, compressed)
            if process.wait() != 0:
                raise RuntimeError("docker save failed")
    from price_model import PricePredictor
    smoke = {"make_name": "Toyota", "model_name": "Camry", "vehicle_age": 5,
             "mileage": 60000, "has_accidents": "false"}
    metadata = {"release": args.release, "source_commit": args.release.split("-")[0],
                "model_sha256": manifest["files"]["artifacts/price_model.joblib"],
                "smoke_input": smoke, "expected_price": PricePredictor().predict(**smoke)["predicted_price"]}
    (work / "release.json").write_text(json.dumps(metadata, indent=2))
    archive = work / "release.tar"
    with tarfile.open(archive, "w") as tar:
        tar.add(work / "images.tar.gz", arcname="images.tar.gz")
        tar.add(ROOT / "deploy/compose.yaml", arcname="deploy/compose.yaml")
        tar.add(work / "release.json", arcname="release.json")
    with archive.open("rb") as source:
        sha = hashlib.file_digest(source, "sha256").hexdigest()
    session.client("s3").upload_file(str(archive), config["bucket"], f"releases/{args.release}.tar")
    # The script and its fixed arguments come from the reviewed commit, not PR text.
    script = (ROOT / "deploy/cd/activate.sh").read_text()
    command = "bash -s -- " + " ".join(shlex.quote(value) for value in (
        config["bucket"], args.release, sha, config["region"])) + " <<'USEDCAR_CD_SCRIPT'\n" + script + "\nUSEDCAR_CD_SCRIPT"
    ssm = session.client("ssm")
    response = ssm.send_command(InstanceIds=[config["instance_id"]], DocumentName="AWS-RunShellScript",
        Parameters={"commands": [command], "executionTimeout": ["900"]}, TimeoutSeconds=900,
        Comment=f"usedcar main deploy {args.release}")
    command_id = response["Command"]["CommandId"]
    print(f"SSM deployment command: {command_id}", flush=True)
    deadline = time.monotonic() + 1000
    while time.monotonic() < deadline:
        try:
            result = ssm.get_command_invocation(CommandId=command_id, InstanceId=config["instance_id"])
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(5)
            continue
        if result["Status"] == "Success":
            print(result["StandardOutputContent"][-5000:])
            print(f"Deployment healthy: {config['public_url']}")
            return
        if result["Status"] not in ("Pending", "InProgress", "Delayed", "Cancelling"):
            print(result["StandardOutputContent"][-5000:])
            print(result["StandardErrorContent"][-5000:])
            raise RuntimeError(f"Deployment failed: {result['Status']}; check rollback output")
        print(f"Deployment {result['Status']}", flush=True)
        time.sleep(15)
    raise TimeoutError(f"SSM command {command_id} did not finish. Inspect it before retrying.")


if __name__ == "__main__":
    main()
