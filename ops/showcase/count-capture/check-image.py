"""Check count capture in a standalone image against disposable local databases."""

import argparse
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--pg-port", required=True, type=int)
    parser.add_argument("--http-port", required=True, type=int)
    parser.add_argument("--expect", choices=["captured", "blocked"], default="captured")
    args = parser.parse_args()
    container = "h-count-capture-image-check"

    def connect(database, role="postgres"):
        return psycopg.connect(
            host="127.0.0.1",
            port=args.pg_port,
            dbname=database,
            user=role,
            password=role,
            autocommit=True,
        )

    # These databases must come from ops/local/init.sh on a private container.
    with connect("warehouse") as warehouse:
        assert warehouse.execute("SHOW mdp.local_stack").fetchone() == ("on",)
        warehouse.execute(
            (ROOT / "ops/showcase/count-capture/warehouse.sql").read_text()
        )
    with connect("control") as control, connect("control", "control_rt") as holder:
        control.execute((ROOT / "ops/showcase/count-capture/control.sql").read_text())
        control.execute("TRUNCATE control.showcase_relation_count")
        holder.execute("SELECT pg_advisory_lock(hashtext('target-probes'))")
        environment = {
            "PORT": str(args.http_port),
            "MDP_SHOWCASE_PEOPLE": "[]",
            "MDP_SHOWCASE_INVENTORY_ENABLED": "1",
            "MDP_CONTROL_RT_URL": f"postgresql://control_rt:control_rt@127.0.0.1:{args.pg_port}/control",
            "MDP_SHOWCASE_WH_URL": f"postgresql://showcase_wh:showcase_wh@127.0.0.1:{args.pg_port}/warehouse",
        }
        command = [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            container,
            "--network",
            "host",
        ]
        for key in environment:
            command.extend(["--env", key])
        command.append(args.image)
        try:
            subprocess.run(
                command, env=os.environ | environment, check=True, capture_output=True
            )
            deadline = time.monotonic() + 30
            while True:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{args.http_port}/healthz", timeout=1
                    ) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.25)
            # No signed-in viewer or page read starts this job. Keep the probe lock held.
            deadline = time.monotonic() + (5 if args.expect == "blocked" else 20)
            while True:
                row = control.execute(
                    "SELECT row_count::text FROM control.showcase_relation_count "
                    "WHERE relation='marts.mart_chart_history'"
                ).fetchone()
                if row or time.monotonic() >= deadline:
                    break
                time.sleep(0.25)
            logs = subprocess.check_output(
                ["docker", "logs", container], text=True, stderr=subprocess.STDOUT
            )
            passes = [
                json.loads(line)
                for line in logs.splitlines()
                if line.startswith('{"event":"showcase_relation_count_capture"')
            ]
            if args.expect == "blocked":
                assert row is None, (
                    "Baseline unexpectedly captured. Check the selected image."
                )
                print(
                    "Baseline stores no count while target-probes holds its lock. Run the fixed image next."
                )
            else:
                assert row == ("1",), (
                    "Count is missing. Read the capture outcome in the image logs."
                )
                assert len(passes) == 1 and passes[0]["captured"] >= 1, (
                    "Expected one startup outcome. Check the image logs."
                )
                print(json.dumps(passes[0], sort_keys=True))
                # A second pass proves the timer stays live, without replacing a stored fact.
                deadline = time.monotonic() + 75
                while len(passes) < 2 and time.monotonic() < deadline:
                    time.sleep(1)
                    logs = subprocess.check_output(
                        ["docker", "logs", container],
                        text=True,
                        stderr=subprocess.STDOUT,
                    )
                    passes = [
                        json.loads(line)
                        for line in logs.splitlines()
                        if line.startswith('{"event":"showcase_relation_count_capture"')
                    ]
                assert len(passes) == 2, (
                    "The timer has no second outcome. Check the image logs."
                )
                assert passes[1]["captured"] == 0 and any(
                    skipped
                    == {
                        "relation": "marts.mart_chart_history",
                        "reason": "already_captured",
                    }
                    for skipped in passes[1]["skipped"]
                ), "The retry must keep the stored count. Check its outcome."
                print(json.dumps(passes[1], sort_keys=True))
                print(
                    "Image captures on startup and retries once without a viewer. Open ops/showcase/count-capture/README.md."
                )
        finally:
            subprocess.run(
                ["docker", "rm", "--force", "--volumes", container],
                check=False,
                capture_output=True,
            )


if __name__ == "__main__":
    main()
