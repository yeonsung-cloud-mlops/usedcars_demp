"""Package/restore trusted model fixtures without committing them or retraining in CD."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "deploy/cd/model-manifest.json"
FILES = ("artifacts/price_model.joblib", "artifacts/prediction_examples.json",
         "artifacts/baseline_4features/price_model.joblib")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pack(root=ROOT):
    """Deterministic bytes: local timestamps/users never change the artifact version."""
    output = io.BytesIO()
    hashes = {}
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0, filename="") as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for name in FILES:
                data = (root / name).read_bytes()
                hashes[name] = digest(data)
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(data))
    data = output.getvalue()
    sha = digest(data)
    return data, {"sha256": sha, "key": f"models/{sha}.tar.gz", "files": hashes}


def restore(data, manifest, destination):
    if digest(data) != manifest["sha256"]:
        raise ValueError("Model artifact checksum mismatch")
    if set(manifest["files"]) != set(FILES):
        raise ValueError("Unexpected model manifest file list")
    verified = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        if len(members) != len(FILES) or {member.name for member in members} != set(FILES):
            raise ValueError("Unexpected model archive file list")
        for member in members:
            if not member.isfile() or member.size > 30_000_000:
                raise ValueError("Invalid model archive member")
            payload = archive.extractfile(member).read()
            if digest(payload) != manifest["files"][member.name]:
                raise ValueError("Model file checksum mismatch")
            target = destination / member.name
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError("Model target escapes destination")
            verified[target] = payload
    # Nothing is written until every checksum and member has been checked.
    for target, payload in verified.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["pack", "download"])
    args = parser.parse_args()
    if args.action == "pack":
        data, manifest = pack()
        target = ROOT / "deploy/.local/model-bundle.tar.gz"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"Packaged {len(data)} bytes; SHA-256 {manifest['sha256']}")
    else:
        import boto3
        config = json.loads((ROOT / "deploy/cd/config.json").read_text())
        manifest = json.loads(MANIFEST.read_text())
        s3 = boto3.client("s3", region_name=config["region"])
        body = s3.get_object(Bucket=config["bucket"], Key=manifest["key"])["Body"]
        try:
            data = body.read(40_000_001)
        finally:
            body.close()
        if len(data) > 40_000_000:
            raise ValueError("Model artifact exceeds size limit")
        restore(data, manifest, ROOT)
        print("Verified model and test fixtures restored")


if __name__ == "__main__":
    main()
