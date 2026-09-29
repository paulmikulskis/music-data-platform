"""Run deploy ordering with a pinned archive and local stand-ins for remote commands."""

import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SHA = "a" * 40


def write(root, name, body, executable=False):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    if executable:
        path.chmod(0o755)
    return path


@pytest.fixture
def deploy(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    for name in ("ops/deploy.sh", "ops/fly/build-context.py"):
        target = checkout / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, target)
    for name in (
        "ops/fly/core-runner/Dockerfile",
        "ops/fly/functions/Dockerfile",
        "control/apps/control-api/Dockerfile",
        "control/apps/data-api/Dockerfile",
        "control/apps/showcase/Dockerfile",
        "ops/fly/postgres/Dockerfile",
        "ops/fly/haproxy/Dockerfile",
        "ops/fly/alloy/Dockerfile",
    ):
        write(checkout, name, "FROM scratch\nCOPY control/ /app/control/\n")
    for app in ("showcase", "data-api"):
        write(checkout, f"ops/fly/{app}/fly.toml", 'dockerfile = "Dockerfile"\n')
    write(checkout, "ops/ci-wait.sh", 'echo ci >> "$TEST_EVENTS"\n')
    write(
        checkout,
        "ops/fly/secret-map.py",
        "import sys\nif sys.argv[1] == 'filter': sys.stdin.read()\n",
    )
    write(
        checkout,
        "ops/fly/fly.sh",
        """case "$1" in
secrets) cat >/dev/null ;;
deploy)
  echo replace >> "$TEST_EVENTS"
  if [[ "$TEST_APP" == mdp-showcase ]]; then test -f "$2/validated" || exit 90; fi
  test -z "$(find "$2" -name '.showcase-overlay-*' -print)" || exit 91
  exit 79 ;;
*) exit 92 ;;
esac
""",
    )
    # The archive has its own collector and validator. Worktree copies must never run.
    collector = """#!/usr/bin/env bash
set -euo pipefail
[[ "$PWD" != "$TEST_CHECKOUT" ]]
[[ "$1" == --dated-only && "$2" == --output ]]
echo collect >> "$TEST_EVENTS"
if [[ "$TEST_FAILURE" == collect ]]; then echo private-sentinel >&2; exit 1; fi
printf 'pinned-stack' > "$3"
"""
    validator = """import argparse, os
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--context', type=Path)
p.add_argument('--overlay', type=Path)
p.add_argument('--revision')
a = p.parse_args()
assert a.context != Path(os.environ['TEST_CHECKOUT'])
assert a.revision == 'a' * 40
with open(os.environ['TEST_EVENTS'], 'a') as f: f.write('validate\\n')
assert a.overlay.joinpath('stack.generated.json').read_text() == 'pinned-stack'
assert os.environ['TEST_FAILURE'] != 'validate', 'private-sentinel'
a.context.joinpath('validated').touch()
"""
    # The archive's own link collector: it runs on every showcase deploy, also with an overlay.
    links = """import argparse, os
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--repo')
p.add_argument('--revision')
p.add_argument('--output', type=Path)
a = p.parse_args()
assert a.revision == 'a' * 40
with open(os.environ['TEST_EVENTS'], 'a') as f: f.write('links\\n')
a.output.write_text('pinned-links')
"""
    archive = tmp_path / "pinned.tar"
    with tarfile.open(archive, "w") as tar:
        for name, body in {
            "ops/showcase/stack/collect.sh": collector,
            "ops/showcase/artifacts.py": validator,
            "ops/showcase/links/collect.py": links,
        }.items():
            data = body.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    write(checkout, "ops/showcase/stack/collect.sh", "exit 93\n")
    write(
        checkout, "ops/showcase/artifacts.py", "raise RuntimeError('worktree copy')\n"
    )
    bin_dir = tmp_path / "bin"
    write(
        bin_dir,
        "git",
        f"#!{sys.executable}\n"
        + """import os, sys
from pathlib import Path
if sys.argv[1:3] == ['rev-parse', '--is-shallow-repository']: print('false')
elif sys.argv[1] == 'rev-parse': print('a' * 40)
elif sys.argv[1] == 'archive':
    assert sys.argv[2] == 'a' * 40
    with open(os.environ['TEST_EVENTS'], 'a') as f: f.write('archive\\n')
    sys.stdout.buffer.write(Path(os.environ['TEST_ARCHIVE']).read_bytes())
else: assert sys.argv[1] == 'status'
""",
        True,
    )
    write(
        bin_dir,
        "fly",
        """#!/bin/sh
case "$1" in
version) echo 'fly v0.4.108 linux/amd64' ;;
orgs) echo '["example-org"]' ;;
apps) echo '[{"Name":"mdp-showcase"},{"Name":"mdp-data-api"}]' ;;
*) exit 94 ;;
esac
""",
        True,
    )
    write(bin_dir, "secret_store", "#!/bin/sh\necho '{}'\n", True)
    write(
        bin_dir,
        "uv",
        f"#!{sys.executable}\n"
        + """import os, sys
assert sys.argv[1:3] == ['run', '--project']
assert sys.argv[4] == 'python'
os.execv(sys.executable, [sys.executable, *sys.argv[5:]])
""",
        True,
    )
    # The showcase deploy counts tenants before collecting links; a fixture control has none.
    write(bin_dir, "pnpm", '#!/bin/sh\necho "[]"\n', True)
    for name in ("psql", "docker"):
        write(
            bin_dir, name, '#!/bin/sh\necho database >> "$TEST_EVENTS"\nexit 95\n', True
        )
    events = tmp_path / "events"
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FLY_ORG": "example-org",
        "SECRET_STORE_PROJECT": "synthetic", "SECRET_STORE_CONFIG": "test",
        "TEST_EVENTS": str(events),
        "TEST_CHECKOUT": str(checkout),
        "TEST_ARCHIVE": str(archive),
        "TEST_FAILURE": "",
        "TEST_APP": "mdp-showcase",
        # A synthetic name list: the deploy refuses the showcase without one.
        "MDP_SHOWCASE_DENY_NAMES": "synthetic-deny-name",
    }

    def run(*, failure="", override=False, app="mdp-showcase", tenants=None):
        values = env | {"TEST_FAILURE": failure, "TEST_APP": app}
        if tenants is not None:
            values["MDP_SHOWCASE_TENANT_COUNT"] = tenants
        if override:
            overlay = tmp_path / "overlay"
            write(overlay, "stack.generated.json", "pinned-stack")
            values["MDP_SHOWCASE_OVERLAY"] = str(overlay)
        result = subprocess.run(
            ["bash", str(checkout / "ops/deploy.sh"), "--app", app],
            env=values,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        return result, events.read_text().splitlines()

    return run


@pytest.mark.parametrize("override", [False, True])
def test_showcase_prepares_pinned_overlay_before_replacement(deploy, override):
    result, events = deploy(override=override)
    assert result.returncode == 79, result.stdout + result.stderr
    assert events == [
        "ci",
        "archive",
        *([] if override else ["collect"]),
        "links",
        "validate",
        "replace",
    ]
    assert "database" not in events


@pytest.mark.parametrize("failure", ["collect", "validate"])
def test_artifact_failure_stops_before_image_replacement(deploy, failure):
    result, events = deploy(failure=failure)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "replace" not in events
    assert "private-sentinel" not in result.stdout + result.stderr
    # The deploy names a user-only log; the collector's own output stays in it.
    message = result.stderr.splitlines()[-1]
    assert message.startswith("Showcase artifact preparation failed. Read ")
    assert message.endswith(", then follow ops/showcase/README.md#recover.")
    log = Path(
        message.removeprefix("Showcase artifact preparation failed. Read ").split(
            ", then follow"
        )[0]
    )
    assert "private-sentinel" in log.read_text()
    assert oct(log.stat().st_mode & 0o777) == oct(0o600)
    log.unlink()


def test_other_app_needs_no_showcase_overlay(deploy):
    result, events = deploy(app="mdp-data-api")
    assert result.returncode == 79, result.stdout + result.stderr
    assert events == ["ci", "archive", "replace"]


def test_operator_tenant_count_skips_the_control_api(deploy, tmp_path):
    # A preset count wins even when the CLI cannot reach the control API.
    (tmp_path / "bin" / "pnpm").write_text("#!/bin/sh\nexit 7\n")
    result, _ = deploy(tenants="0")
    assert result.returncode == 79, result.stdout + result.stderr
    assert "links_tenants_unknown" not in result.stderr
