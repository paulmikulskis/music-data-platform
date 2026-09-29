"""Replay fixed song rules locally. Start with: run.py capture --help."""

import argparse
import fcntl
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ops.backtest import warehouse
from ops.backtest.errors import BacktestError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["capture", "replay", "label", "score", "report", "weekly"]
    )
    parser.add_argument(
        "--method",
        action="append",
        help="Name=git-ref; repeat to compare fixed methods",
    )
    parser.add_argument(
        "--chosen-on",
        action="append",
        help="Name=YYYY-MM-DD; must match the committed choices.json record",
    )
    parser.add_argument(
        "--chosen-through",
        help="Last UTC day used to choose rules; all reported days must follow it",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "ops/evidence/backtest"
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="With report, remove this run's container and private capture",
    )
    args = parser.parse_args()
    os.umask(0o077)
    scratch = Path(
        os.environ.get("MDP_BACKTEST_SCRATCH", "/tmp/mdp-backtest")
    ).resolve()
    if (
        scratch == ROOT
        or ROOT in scratch.parents
        or scratch == ROOT.parent
        or scratch == Path("/")
        or any((parent / ".git").exists() for parent in (scratch, *scratch.parents))
    ):
        parser.error(
            "Scratch must be outside the repository. Set MDP_BACKTEST_SCRATCH=/tmp/mdp-backtest."
        )
    scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    lock = (scratch / ".lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        work = scratch / "results"
        work.mkdir(exist_ok=True)
        if args.action == "weekly":
            from ops.backtest import evaluate

            try:
                warehouse.capture(scratch, args.method, work, args.chosen_on)
                warehouse.replay(scratch, args.method, work, args.chosen_on)
                state = json.loads((scratch / "state.json").read_text())
                with warehouse.local_connection(state) as conn:
                    evaluate.label(conn, state, work)
                    evaluate.score(conn, state, args.chosen_through, work)
                for name in ("report.json", "labels.json"):
                    shutil.copyfile(work / name, args.output / name)
                evaluate.report(state, args.output)
            finally:
                state_path = scratch / "state.json"
                if state_path.exists():
                    state = json.loads(state_path.read_text())
                    if "container" in state:
                        warehouse.cleanup(scratch, state, keep_choices=True)
                shutil.rmtree(work, ignore_errors=True)
        elif args.action == "capture":
            warehouse.capture(scratch, args.method, work, args.chosen_on)
        elif args.action == "replay":
            warehouse.replay(scratch, args.method, work, args.chosen_on)
        else:
            from ops.backtest import evaluate

            state = json.loads((scratch / "state.json").read_text())
            with warehouse.local_connection(state) as conn:
                if args.action == "label":
                    evaluate.label(conn, state, work)
                    shutil.copyfile(work / "labels.json", args.output / "labels.json")
                elif args.action == "score":
                    evaluate.score(conn, state, args.chosen_through, args.output)
                else:
                    evaluate.report(state, args.output)
            if args.action == "report" and args.cleanup:
                warehouse.cleanup(scratch, state)
    except BacktestError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, warehouse.psycopg.Error) as exc:
        # Driver errors may include a URL or a copied value. Keep them out of logs.
        print(
            f"Backtest stopped ({type(exc).__name__}). Open ops/backtest/README.md#recover.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
