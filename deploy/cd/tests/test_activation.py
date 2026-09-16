"""Exercise the real activation shell's promotion/rollback with isolated command shims."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from deploy.cd.model_artifact import ROOT


@pytest.mark.parametrize("failure", ["none", "startup", "inference", "rollback"])
def test_promotion_and_rollback(tmp_path, failure):
    base = tmp_path / "service"
    previous = base / "releases/previous"
    (previous / "deploy").mkdir(parents=True)
    (previous / "deploy/.env").write_text("RELEASE=previous\n")
    (base / "current").symlink_to(previous)
    release = "a" * 40 + "-123-1"
    target = base / "releases" / release
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    scripts = {
        "flock": "#!/bin/sh\nexit 0\n",
        "python3": """#!/bin/sh
if [ "$#" = 5 ]; then
  mkdir -p "$TEST_TARGET/deploy"
  touch "$TEST_TARGET/images.tar.gz" "$TEST_TARGET/deploy/compose.yaml" "$TEST_TARGET/release.json"
  printf 'RELEASE=test\\n' > "$TEST_TARGET/deploy/.env"
elif [ "$TEST_FAILURE" = inference ]; then
  exit 1
fi
""",
        "docker": """#!/bin/sh
printf '%s\\n' "$*" >> "$TEST_LOG"
case "$*" in
  *' up '*)
    case "$*" in
      *'/previous/'*) [ "$TEST_FAILURE" != rollback ] ;;
      *) [ "$TEST_FAILURE" != startup ] && [ "$TEST_FAILURE" != rollback ] ;;
    esac ;;
  *) exit 0 ;;
esac
""",
        # macOS mv lacks -T. Keep the production script portable in this isolated test.
        "mv": f"#!{sys.executable}\nimport os, sys\nos.replace(sys.argv[-2], sys.argv[-1])\n",
    }
    for name, content in scripts.items():
        path = bin_dir / name
        path.write_text(content)
        path.chmod(0o755)
    script = tmp_path / "activate.sh"
    script.write_text((ROOT / "deploy/cd/activate.sh").read_text().replace("/opt/usedcar", str(base)))
    log = tmp_path / "docker.log"
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
           "TEST_TARGET": str(target), "TEST_LOG": str(log), "TEST_FAILURE": failure}
    result = subprocess.run(["bash", str(script), "test-bucket", release, "0" * 64, "ap-northeast-2"],
                            env=env, capture_output=True, text=True)
    if failure == "none":
        assert result.returncode == 0, result.stderr
        assert (base / "current").resolve() == target
        assert (base / "previous-release").read_text().strip() == str(previous)
        assert not (target / "images.tar.gz").exists()
    else:
        assert result.returncode != 0
        assert (base / "current").resolve() == previous
        assert str(previous / "deploy/compose.yaml") in log.read_text()
        assert "ROLLBACK FAILED" in result.stderr if failure == "rollback" else "Rollback healthy" in result.stdout
