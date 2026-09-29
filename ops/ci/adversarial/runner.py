"""fail closed on missing rows, skipped detectors, missing output, and bad links."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
CATALOG = json.loads((BASE / "catalog.json").read_text())
OFFLINE = {"dbt", "lint", "registration"}


def clean(value):
    value = re.sub(r"\x1b\[[0-9;]*m", "", str(value))
    for key, secret in sorted(
        os.environ.items(), key=lambda x: len(x[1]), reverse=True
    ):
        if (
            secret
            and len(secret) > 3
            and secret
            not in {
                "postgres",
                "migrator",
                "rights_sync",
                "control_rt",
                "functions_rt",
                "service_read",
                "loader_wh",
                "dbt_transform",
                "workbench_wh",
                "reader_wh",
            }
            and any(
                token in key
                for token in ("PASSWORD", "TOKEN", "SECRET", "_URL", "_KEY")
            )
        ):
            value = value.replace(secret, "<redacted>")
    value = re.sub(r"postgres(?:ql)?://[^\s\"\']+", "<database-url>", value)
    value = re.sub(r"password=[^\s]+", "password=<redacted>", value)
    value = re.sub(r"pytest-of-[^/\s]+", "pytest-of-operator", value)
    return re.sub(r"/(?:Users|home)/[^/\s]+", "<home>", value)


def inventory():
    assert len({c["slug"] for c in CATALOG}) == len(CATALOG), "duplicate case slug"
    actual = {p.name for p in BASE.iterdir() if p.is_dir() and (p / "case.md").exists()}
    assert actual == {c["slug"] for c in CATALOG}, (
        "unregistered or missing case directory"
    )
    for c in CATALOG:
        directory = BASE / c["slug"]
        for filename in ("case.md", "fixture.json", "run.sh", "expected.txt"):
            assert (directory / filename).is_file(), f"{c['slug']}: missing {filename}"
        assert (directory / "expected.txt").read_text().strip(), "empty expected output"
        fixture = json.loads((directory / "fixture.json").read_text())
        assert fixture == {k: c[k] for k in ("kind", "target")}, (
            "fixture differs from catalog"
        )
        runbook = ROOT / "ops/runbooks" / f"{c['runbook'].replace('-', '_')}.md"
        assert runbook.is_file(), f"missing runbook {c['runbook']}"
        assert (
            f"../../../runbooks/{runbook.name}" in (directory / "case.md").read_text()
        ), "missing runbook link"
        assert f"`{c['rule']}`" in (directory / "case.md").read_text(), "missing rule"


def process(argv, env=None, timeout=1200):
    result = subprocess.run(
        argv,
        cwd=ROOT,
        env=os.environ | (env or {}),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return result.returncode, clean(result.stdout + result.stderr)


def execute(case, evidence, cache):
    directory = BASE / case["slug"]
    if case["kind"] in OFFLINE:
        from offline import execute as offline

        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            try:
                offline(case, directory)
                code = 0
            except Exception as exc:  # noqa: BLE001 - independent cases must still produce evidence
                print(type(exc).__name__ + ": " + str(exc))
                code = 1
        return code, clean(output.getvalue())
    if case["kind"] == "pytest":
        if not os.environ.get("MDP_CONTROL_URL"):
            return (
                1,
                "integration environment missing; skipped tests never count as PASS",
            )
        report = evidence / (case["slug"] + ".xml")
        code, output = process(
            [
                "uv",
                "run",
                "--project",
                "functions",
                "pytest",
                "functions/tests/" + case["target"],
                "-vv",
                "-s",
                "-rA",
                "--tb=short",
                "--color=no",
                "-p",
                "pytest_audit",
                "--junitxml=" + str(report),
            ],
            {
                "MDP_FIXTURE_MODE": "0",
                "MDP_ADVERSARIAL_AUDIT": "1",
                "PYTHONPATH": str(BASE) + os.pathsep + os.environ.get("PYTHONPATH", ""),
            },
            timeout=300,
        )
        if report.exists():
            import xml.etree.ElementTree as ET

            tree = ET.parse(report)
            sanitized = clean(report.read_text())
            for marker in ("home", "redacted", "database-url"):
                sanitized = sanitized.replace(
                    "<" + marker + ">", "&lt;" + marker + "&gt;"
                )
            sanitized = re.sub(
                r'hostname="[^"]*"', 'hostname="local-fixture"', sanitized
            )
            report.write_text(sanitized)
            tests = tree.findall(".//testcase")
            if not tests or tree.findall(".//skipped"):
                code = 1
                output += "\nFAIL no executed tests or unexpected skips\n"
        else:
            code = 1
        return code, output
    if case["kind"] == "lifecycle":
        letter = case["target"]
        shared = evidence / ("lifecycle-" + letter + ".json")
        if letter not in cache and shared.exists():
            cache[letter] = json.loads(shared.read_text())
        if letter not in cache:
            path = evidence / ("lifecycle-" + letter)
            path.mkdir()
            code, output = process(
                [
                    "uv",
                    "run",
                    "--project",
                    "functions",
                    "python",
                    "ops/ci/adversarial/lifecycle.py",
                    "--target",
                    "pg_local",
                    "--case",
                    letter,
                    "--evidence-dir",
                    str(path),
                ]
            )
            full = path / f"lifecycle-{letter}.txt"
            cache[letter] = (
                code,
                output
                + "\n"
                + (full.read_text() if full.exists() else "missing lifecycle evidence"),
            )
            shared.write_text(json.dumps(cache[letter]))
        return cache[letter]
    from live import execute as live

    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        try:
            live(case, evidence)
            code = 0
        except Exception as exc:  # noqa: BLE001 - independent cases must still produce evidence
            print(type(exc).__name__ + ": " + str(exc))
            code = 1
    return code, clean(output.getvalue())


def run_case(case, evidence, cache):
    try:
        code, output = execute(case, evidence, cache)
        expected = (BASE / case["slug"] / "expected.txt").read_text().splitlines()
        missing = [line for line in expected if line not in output]
        passed = code == 0 and not missing
        failures = [
            line
            for line in output.splitlines()
            if "ASSERT FAIL:" in line
            or line.startswith(("FAILED ", "AssertionError:", "RuntimeError:", "CASE "))
            and "PASS" not in line
        ]
        detail = (
            "all expected detector output matched"
            if passed
            else "; ".join(failures[-2:] or [f"detector exit={code}"])
        )
        if missing:
            detail += "; missing: " + ", ".join(missing)
    except Exception as exc:  # noqa: BLE001 - independent cases must still produce evidence
        passed = False
        output = clean(f"{type(exc).__name__}: {exc}")
        detail = output
    line = f"CASE {case['slug']} {'PASS' if passed else 'FAIL'} rule={case['rule']} runbook={case['runbook']} {clean(detail).replace(chr(10), ' ')[:900]}"
    (evidence / (case["slug"] + ".txt")).write_text(clean(output) + "\n" + line + "\n")
    print(line, flush=True)
    return passed, line


def wrapped_case(case, evidence, nonce):
    """Execute the checked-in wrapper and validate its actual output attribution."""
    try:
        code, output = process(
            [str(BASE / case["slug"] / "run.sh")],
            {
                "MDP_ADVERSARIAL_SUITE_DIR": str(evidence),
                "MDP_ADVERSARIAL_SUITE_NONCE": nonce,
            },
        )
        lines = [line for line in output.splitlines() if line.startswith("CASE ")]
        pattern = (
            r"CASE "
            + re.escape(case["slug"])
            + r" (PASS|FAIL) rule="
            + re.escape(case["rule"])
            + r" runbook="
            + re.escape(case["runbook"])
            + r" .+"
        )
        if len(lines) != 1 or not re.fullmatch(pattern, lines[0]):
            raise AssertionError(
                "case wrapper omitted/duplicated its outcome, rule, or runbook"
            )
        line = lines[0]
        passed = " PASS " in line and code == 0
        if " PASS " in line and code != 0:
            raise AssertionError("case wrapper printed PASS but exited nonzero")
    except Exception as exc:  # noqa: BLE001 - broken wrappers are reported, never omitted
        passed = False
        line = f"CASE {case['slug']} FAIL rule={case['rule']} runbook={case['runbook']} {clean(str(exc))}"
    print(line, flush=True)
    return passed, line


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--case")
    group.add_argument("--all", action="store_true")
    group.add_argument("--accept", action="store_true")
    parser.add_argument("--offline-only", action="store_true")
    args = parser.parse_args()
    if args.accept and args.offline_only:
        parser.error("acceptance requires every case")
    shared_dir = os.environ.get("MDP_ADVERSARIAL_SUITE_DIR")
    if shared_dir:
        assert args.case, "shared suite is only valid for an individual wrapper"
        evidence = Path(shared_dir).resolve()
        assert evidence.parent == ROOT / "ops/evidence/adversarial", (
            "invalid suite evidence root"
        )
        marker = json.loads((evidence / "suite.json").read_text())
        assert marker["nonce"] == os.environ["MDP_ADVERSARIAL_SUITE_NONCE"]
        os.kill(marker["pid"], 0)  # Refuse cache reuse after its parent suite exits.
        inventory()
        case = next(c for c in CATALOG if c["slug"] == args.case)
        return 0 if run_case(case, evidence, {})[0] else 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    evidence = ROOT / "ops/evidence/adversarial" / ("run-" + stamp)
    evidence.mkdir(parents=True)
    try:
        inventory()
    except Exception as exc:  # noqa: BLE001 - inventory failure is acceptance evidence
        message = "INVENTORY FAIL " + clean(str(exc)) + "\n"
        (evidence.parent / ("adversarial-" + stamp + ".txt")).write_text(message)
        if args.accept:
            message += "ACCEPT adversarial FAIL\n"
            (evidence.parent / ("accept-adversarial-" + stamp + ".txt")).write_text(message)
        print(message, end="", flush=True)
        return 1
    selected = [
        c
        for c in CATALOG
        if (not args.case or c["slug"] == args.case)
        and (not args.offline_only or c["kind"] in OFFLINE)
    ]
    if not selected:
        parser.error("unknown case")
    results = []
    nonce = uuid4().hex
    marker = evidence / "suite.json"
    marker.write_text(json.dumps({"pid": os.getpid(), "nonce": nonce}))
    try:
        for c in selected:
            print(f"RUN {c['number']:02d}/{len(CATALOG)} {c['slug']}", flush=True)
            results.append(wrapped_case(c, evidence, nonce))
    finally:
        marker.unlink()

    table = ["| # | Case | Result | Rule | Runbook |", "|---|---|---|---|---|"]
    metadata_valid = True
    for case, (passed, line) in zip(selected, results):
        if not re.fullmatch(
            r"CASE "
            + re.escape(case["slug"])
            + r" (PASS|FAIL) rule="
            + re.escape(case["rule"])
            + r" runbook="
            + re.escape(case["runbook"])
            + r" .+",
            line,
        ):
            passed = False
            metadata_valid = False
        table.append(
            f"| {case['number']} | {case['slug']} | {'PASS' if passed else 'FAIL'} | {case['rule']} | {case['runbook']} |"
        )
    report = ROOT / "ops/evidence/adversarial" / ("adversarial-" + stamp + ".txt")
    summary = "\n".join(table) + "\n\n" + "\n".join(line for _, line in results) + "\n"
    report.write_text(summary)
    print("\n".join(table), flush=True)
    print("EVIDENCE " + str(report.relative_to(ROOT)), flush=True)
    passed = metadata_valid and all(ok for ok, _ in results)
    if args.accept:
        from audit import audit

        acceptance = ROOT / "ops/evidence/adversarial" / ("accept-adversarial-" + stamp + ".txt")
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            try:
                inventory()
                audit()
            except Exception as exc:  # noqa: BLE001 - independent cases must still produce evidence
                passed = False
                print("AUDIT FAIL " + clean(f"{type(exc).__name__}: {exc}"))
        final = "ACCEPT adversarial " + ("PASS" if passed else "FAIL")
        acceptance.write_text(
            summary + "\n" + clean(stream.getvalue()) + "\n" + final + "\n"
        )
        print(clean(stream.getvalue()), end="")
        print("EVIDENCE " + str(acceptance.relative_to(ROOT)), flush=True)
        print(final, flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
