"""Check glossary order offline; run with python3 ops/ci/check_glossary.py."""

import argparse
from pathlib import Path

GLOSSARY = Path(__file__).resolve().parents[2] / "docs/glossary.md"


def check(path):
    previous = None
    count = 0
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.startswith("|"):
            continue
        term = line.split("|")[1].strip()
        if term == "Term" or not term.strip("-: "):
            continue
        key = term.strip("`").lstrip("_").casefold()
        if previous and key < previous[0]:
            raise ValueError(
                f"{path}:{number}: {term} is out of order; "
                f"move it before {previous[1]} in the Term column."
            )
        previous = key, term
        count += 1
    if not count:
        raise ValueError(f"{path}: no terms found; restore the Term table.")
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=GLOSSARY)
    args = parser.parse_args()
    try:
        count = check(args.path)
    except ValueError as exc:
        parser.exit(1, f"FAIL {exc}\n")
    print(f"PASS glossary: {count} terms in order; add new terms alphabetically in docs/glossary.md.")


if __name__ == "__main__":
    main()
