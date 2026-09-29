"""Check each serving machine's image revision and local HTTP health before releasing runners."""

import os
import json
import shlex
import subprocess
import sys

COMMANDS = {
    "mdp-functions": (
        "/app/functions/.venv/bin/python -c "
        + shlex.quote(
            "import json,os,urllib.request; "
            "response=urllib.request.urlopen('http://127.0.0.1:8080/v1/health', timeout=10); "
            "print(json.dumps({'revision':os.environ.get('MDP_BUILD_SHA'), "
            "'status':json.load(response).get('status')}))"
        )
    ),
    "mdp-control-api": (
        "node --input-type=module -e "
        + shlex.quote(
            "const response = await fetch('http://[::1]:8090/health', "
            "{ signal: AbortSignal.timeout(10000) }); "
            "if (!response.ok) process.exit(1); "
            "const body = await response.json(); "
            "console.log(JSON.stringify({ revision: process.env.MDP_BUILD_SHA, status: body.status }));"
        )
    ),
}
RECOVER = (
    "ops/deploy.sh --app mdp-core-runner --app mdp-functions "
    "--app mdp-control-api --app mdp-data-api"
)


def fly(app, *args):
    return subprocess.run(
        ["bash", "ops/fly/fly.sh", *args, "--org", os.environ.get("FLY_ORG", "example-org"), "--app", app],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout


def check(app, revision):
    machines = json.loads(fly(app, "machine", "list", "--json"))
    if not isinstance(machines, list) or any(
        not isinstance(machine, dict) for machine in machines
    ):
        raise ValueError("invalid machine list")
    serving = [
        machine
        for machine in machines
        if machine.get("state") != "destroyed"
        and (
            app != "mdp-functions"
            or machine.get("config", {}).get("metadata", {}).get("fly_process_group")
            == "api"
        )
    ]
    if not serving or any(machine.get("state") != "started" for machine in serving):
        raise ValueError("service is not running")
    for machine in serving:
        result = json.loads(
            fly(
                app,
                "ssh",
                "console",
                "--machine",
                machine["id"],
                "--command",
                COMMANDS[app],
            )
        )
        if (
            not isinstance(result, dict)
            or result.get("revision") != revision
            or result.get("status") != "ok"
        ):
            raise ValueError("service revision or health differs")
    print(f"HOST {app}.internal revision={revision}; continue with the runner release")


def main():
    for app in COMMANDS:
        try:
            check(app, sys.argv[1])
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            subprocess.SubprocessError,
        ):
            print(
                f"FAIL {app} does not report the pinned release and healthy HTTP service. "
                f"Rerun: {RECOVER}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
