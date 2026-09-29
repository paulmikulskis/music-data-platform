"""Deploy-only inputs are checked without entering Fly imports or diagnostics."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SECRET_MAP = Path(__file__).parents[2] / "ops/fly/secret-map.py"


@pytest.fixture
def secret_map():
    spec = importlib.util.spec_from_file_location("showcase_deploy_inputs", SECRET_MAP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("deny", [None, "", " \n\t"])
def test_check_refuses_missing_deploy_names(secret_map, capsys, deny):
    values = dict.fromkeys(secret_map.MAP["mdp-showcase"], "synthetic-runtime-value")
    values["MDP_SHOWCASE_DENY_NAMES"] = deny
    with pytest.raises(SystemExit) as failure:
        secret_map.check("mdp-showcase", values)
    assert failure.value.code == 1
    output = capsys.readouterr()
    assert "FAIL deploy input mdp-showcase: MDP_SHOWCASE_DENY_NAMES" in output.err
    assert "ops/fly/SECRETS.md#showcase" in output.err
    assert "synthetic-runtime-value" not in output.out + output.err


def test_check_lists_local_prerequisites_without_reading_them(secret_map, capsys):
    values = dict.fromkeys(secret_map.MAP["mdp-showcase"], "synthetic-runtime-value")
    values["MDP_SHOWCASE_DENY_NAMES"] = "synthetic-denied-value\nsecond-synthetic-value"
    secret_map.check("mdp-showcase", values)
    output = capsys.readouterr()
    assert "PASS required secrets mdp-showcase" in output.out
    assert "full git history" in output.err
    assert "MDP_SHOWCASE_TENANT_COUNT" in output.err
    assert "mdp tenants list" in output.err
    assert "docs/operating.md#showcase-deploy-inputs" in output.err
    for value in values.values():
        assert value not in output.out + output.err


def test_filter_excludes_deploy_inputs():
    result = subprocess.run(
        [sys.executable, str(SECRET_MAP), "filter", "mdp-showcase"],
        input=json.dumps(
            {
                "MDP_SHOWCASE_READER_KEY": "synthetic-reader",
                "MDP_SHOWCASE_DENY_NAMES": "synthetic-denied-value\nsecond-synthetic-value",
                "MDP_SHOWCASE_TENANT_COUNT": "0",
                "MDP_SHOWCASE_PROBE_HOSTS": "1",
            }
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == 'MDP_SHOWCASE_READER_KEY="synthetic-reader"\n'
    assert result.stderr == ""
