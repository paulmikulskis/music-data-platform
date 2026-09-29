"""Export source readers from function declarations. Run with the functions environment."""

import argparse
import json
import os
from pathlib import Path

from mdp_functions.registry import discover

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "control/apps/showcase/lib/source-registry.generated.json"


def generate():
    # Fixture-only declarations are not places production music data comes from.
    os.environ.pop("MDP_FIXTURE_MODE", None)
    return {
        key: {
            "layer": item.layer,
            "external": item.external,
            "source": item.kind == "invoke"
            and item.external
            and not item.llm_step
            and not item.model_steps,
        }
        for key, item in sorted(discover().items())
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = json.dumps(generate(), indent=2) + "\n"
    if args.check:
        if OUTPUT.read_text() != rendered:
            raise SystemExit(
                "Source registry drift. Run uv run --project functions python ops/showcase/source_registry.py."
            )
    else:
        OUTPUT.write_text(rendered)


if __name__ == "__main__":
    main()
