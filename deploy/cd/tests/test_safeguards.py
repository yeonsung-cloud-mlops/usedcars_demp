import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

from deploy.cd.bootstrap import policies
from deploy.cd.deploy import validate_release
from deploy.cd.model_artifact import FILES, ROOT, digest, pack, restore


def fixture_bundle(tmp_path):
    for name in FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((name + " fixture").encode())
    return pack(tmp_path)


def test_model_bundle_is_deterministic_and_checks_every_file(tmp_path):
    data, manifest = fixture_bundle(tmp_path)
    assert pack(tmp_path) == (data, manifest)
    restored = tmp_path / "restored"
    restore(data, manifest, restored)
    assert all((restored / name).read_bytes() == (tmp_path / name).read_bytes() for name in FILES)
    with pytest.raises(ValueError, match="checksum"):
        restore(data + b"modified", manifest, tmp_path / "bad")
    changed = {**manifest, "files": {**manifest["files"], FILES[-1]: "0" * 64}}
    with pytest.raises(ValueError, match="checksum"):
        restore(data, changed, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize("member_name,kind", [("../escape", tarfile.REGTYPE),
                                             (FILES[0], tarfile.SYMTYPE)])
def test_archive_rejects_traversal_and_symlinks(tmp_path, member_name, kind):
    _, manifest = fixture_bundle(tmp_path)
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        for index, name in enumerate(FILES):
            info = tarfile.TarInfo(member_name if index == 0 else name)
            info.type = kind if index == 0 else tarfile.REGTYPE
            info.linkname = "../../outside" if kind == tarfile.SYMTYPE and index == 0 else ""
            archive.addfile(info, io.BytesIO(b""))
    data = out.getvalue()
    with pytest.raises(ValueError):
        restore(data, {**manifest, "sha256": digest(data)}, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize("release", ["../escape", "main", "a" * 40 + "; id", "a" * 40 + "-1-1\n", ""])
def test_release_id_rejects_shell_and_path_input(release):
    with pytest.raises(ValueError):
        validate_release(release)


def test_iam_is_limited_to_main_target_instance_and_s3_prefixes():
    config = json.loads((ROOT / "deploy/cd/config.json").read_text())
    workflow, instance, operator, trust = policies(config)
    assert trust["Statement"][0]["Condition"]["StringEquals"]["token.actions.githubusercontent.com:sub"] == config["oidc_subject"]
    assert config["oidc_subject"].endswith(":ref:refs/heads/main")
    send = next(item for item in workflow["Statement"] if item["Action"] == "ssm:SendCommand")
    assert send["Resource"] == [f"arn:aws:ec2:{config['region']}:{config['account_id']}:instance/{config['instance_id']}",
                                 f"arn:aws:ssm:{config['region']}::document/AWS-RunShellScript"]
    assert instance["Statement"][0]["Action"] == "s3:GetObject"
    assert instance["Statement"][0]["Resource"].endswith("/releases/*")
    for policy in (workflow, instance, operator):
        for statement in policy["Statement"]:
            actions = statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
            assert all(not action.startswith("iam:") and action != "*" for action in actions)


def test_activation_shell_parses_and_rejects_invalid_argument():
    script = ROOT / "deploy/cd/activate.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    result = subprocess.run(["bash", str(script), "valid-bucket", "../bad", "0" * 64, "ap-northeast-2"], capture_output=True)
    assert result.returncode != 0
