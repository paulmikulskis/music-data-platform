"""Small local pre-PR checks. CI remains the full safety net."""

import argparse
import ast
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DBT_LOCK = Lock()
UV = ["uv", "run", "--project", str(ROOT / "functions")]


def run(args, *, cwd=ROOT, env=None, ok=(0,)):
    result = subprocess.run(
        args, cwd=cwd, env=env, text=True, capture_output=True, check=False
    )
    if result.returncode not in ok:
        raise RuntimeError(
            " ".join(map(str, args)) + "\n" + result.stdout + result.stderr
        )
    return result.stdout


def changed_paths(base):
    # Comparing with the worktree includes committed, staged and unstaged edits.
    tracked = run(["git", "diff", "--name-only", "--no-renames", "-z", base]).split(
        "\0"
    )
    new = run(["git", "ls-files", "--others", "--exclude-standard", "-z"]).split("\0")
    return sorted(set(tracked + new) - {""})


def module_name(path):
    return str(path.with_suffix("")).replace("/", ".").removesuffix(".__init__")


def imports(path, module):
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = node.module or ""
            if node.level:
                package = (
                    module.split(".")
                    if path.name == "__init__.py"
                    else module.split(".")[:-1]
                )
                prefix = ".".join(
                    package[: len(package) - node.level + 1]
                    + ([prefix] if prefix else [])
                )
            found.add(prefix)
            found.update(prefix + "." + alias.name for alias in node.names)
    return found


def function_tests(paths):
    source = ROOT / "functions/src"
    modules = {module_name(p.relative_to(source)): p for p in source.rglob("*.py")}
    modules.update({p.stem: p for p in (ROOT / "functions/tests").glob("*.py")})
    changed = {
        module_name(Path(p).relative_to("functions/src"))
        for p in paths
        if p.startswith("functions/src/") and p.endswith(".py")
    }
    changed |= {
        Path(p).stem
        for p in paths
        if p.startswith("functions/tests/") and p.endswith(".py")
    }
    # Fixture edits belong to their source declaration.
    for p in paths:
        if p.startswith("functions/src/mdp_functions/sources/"):
            changed.add(".".join(Path(p).parts[2:5]) + ".function")
    affected = set(changed)
    dependencies = {name: imports(path, name) for name, path in modules.items()}
    while True:
        more = {name for name, deps in dependencies.items() if deps & affected}
        if more <= affected:
            break
        affected |= more
    tests = sorted((ROOT / "functions/tests").glob("test_*.py"))
    selected = {
        str(p.relative_to(ROOT))
        for p in tests
        if imports(p, p.stem) & affected or str(p.relative_to(ROOT)) in paths
    }
    # Registry discovery is dynamic. Tests naming a touched source also cover its declaration.
    keys = {
        p.split("/")[4]
        for p in paths
        if p.startswith("functions/src/mdp_functions/sources/")
    }
    for path in tests:
        name = str(path.relative_to(ROOT))
        if name in selected or not keys:
            continue
        tree = ast.parse(path.read_text())

        def mentions_source(node):
            return any(
                isinstance(n, ast.Constant)
                and isinstance(n.value, str)
                and any(key in n.value for key in keys)
                for n in ast.walk(node)
            )

        cases = [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        ]
        # A shared fixture, constant or test class can affect the whole file.
        if any(mentions_source(node) for node in tree.body if node not in cases):
            selected.add(name)
        else:
            selected.update(
                name + "::" + node.name for node in cases if mentions_source(node)
            )
    broad = any(
        p
        in (
            "functions/pyproject.toml",
            "functions/uv.lock",
            "functions/tests/conftest.py",
        )
        for p in paths
    )
    if broad or "conftest" in affected or not selected:
        return [str(p.relative_to(ROOT)) for p in tests]
    if any(
        p.split("::")[0] == "functions/tests/test_reference_probe.py" for p in selected
    ):
        selected |= {
            str(p.relative_to(ROOT))
            for p in tests
            if p.name.startswith("test_identity_")
        }
    return sorted(selected)


def links(paths):
    errors = []
    for name in paths:
        path = ROOT / name
        if not path.is_file() or path.suffix != ".md":
            continue
        text = re.sub(r"```.*?```", "", path.read_text(), flags=re.DOTALL)
        refs = re.findall(r"\]\(([^\s)]+)(?:\s+[^)]*)?\)", text)
        refs += re.findall(r"^\s*\[[^]]+\]:\s*(\S+)", text, re.MULTILINE)
        for ref in refs:
            url = urlsplit(ref.strip("<>"))
            if url.scheme or url.netloc:
                continue
            target = (path.parent / unquote(url.path)).resolve() if url.path else path
            if not target.exists():
                errors.append(f"{name}: missing link {ref}")
            elif url.fragment and target.suffix == ".md":
                headings = re.findall(r"^#+\s+(.+)$", target.read_text(), re.MULTILINE)
                slugs = [
                    re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-")
                    for h in headings
                ]
                if (
                    unquote(url.fragment) not in slugs
                    and f'id="{url.fragment}"' not in target.read_text()
                ):
                    errors.append(f"{name}: missing heading {ref}")
    if errors:
        raise RuntimeError("\n".join(errors))


def lineage_changed(paths):
    return any(
        not p.endswith(".md")
        and (
            p.startswith(("dbt/", "functions/src/", "ops/showcase/lineage/"))
            or p
            in (
                "ops/showcase/queries.json",
                "control/apps/showcase/lib/lineage.generated.json",
            )
        )
        for p in paths
    )


def source_registry_changed(paths):
    return any(
        not p.endswith(".md")
        and (
            p.startswith("functions/src/")
            or p
            in (
                "ops/showcase/source_registry.py",
                "control/apps/showcase/lib/source-registry.generated.json",
            )
        )
        for p in paths
    )


def conformance_changed(paths):
    return any(
        not p.endswith(".md")
        and p.startswith(
            (
                "functions/src/",
                "functions/openapi/",
                "control/",
                "ops/ci/jobs/conformance-contracts.sh",
                ".github/workflows/conformance.yml",
            )
        )
        for p in paths
    )


def needs_drift(paths):
    return (
        lineage_changed(paths)
        or source_registry_changed(paths)
        or conformance_changed(paths)
        or any(
            not p.endswith(".md")
            and p.startswith(
                (
                    "functions/",
                    "docs/sources/",
                    "control/packages/data-sdk/",
                    "control/apps/data-api/src/generated/",
                    "ops/ci/generate_",
                    "ops/label-catalog.py",
                    "ops/analyst-doc.py",
                    "ops/ready.py",
                    "control/packages/contracts/src/health-policy",
                    "control/apps/control-api/src/health-policy",
                )
            )
            for p in paths
        )
    )


def sdk_changed(paths):
    return any(
        p.startswith(
            (
                "dbt/",
                "control/packages/data-sdk/",
                "control/apps/data-api/src/generated/",
            )
        )
        for p in paths
    )


def prepare_control_dependencies():
    command = ["pnpm", "--dir", "control", "install", "--frozen-lockfile"]
    try:
        run(command, cwd=ROOT)
    except (OSError, RuntimeError) as error:
        raise RuntimeError(
            f"Control dependencies are not ready: {error}\n"
            "Run pnpm --dir control install --frozen-lockfile, then bash ops/ready.sh."
        ) from error


def prepare_dbt_profile():
    profile = ROOT / "dbt/profiles/profiles.yml"
    if profile.exists():
        return
    try:
        example = profile.with_name("profiles.example.yml").read_bytes()
        # Exclusive creation also preserves a profile written during this check.
        with profile.open("xb") as output:
            output.write(example)
    except FileExistsError:
        return
    except OSError as error:
        raise RuntimeError(
            f"The local dbt profile is not ready: {error}\n"
            "Run cp -n dbt/profiles/profiles.example.yml dbt/profiles/profiles.yml, "
            "then bash ops/ready.sh."
        ) from error


def drift(paths):
    if sdk_changed(paths) or conformance_changed(paths):
        prepare_control_dependencies()
    # Generators run on a copy of the current files, never overwrite an author's edits.
    with tempfile.TemporaryDirectory(prefix="mdp-ready-drift-") as directory:
        copy = Path(directory)
        names = run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
        ).split("\0")
        for name in names:
            source = ROOT / name
            if (
                not name
                or name.startswith(("ops/evidence/"))
                or not source.is_file()
            ):
                continue
            destination = copy / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        # pnpm resolves from each workspace package, not only the workspace root.
        for parent in [
            ROOT / "control",
            *(ROOT / "control/apps").iterdir(),
            *(ROOT / "control/packages").iterdir(),
        ]:
            if (parent / "node_modules").exists():
                (copy / parent.relative_to(ROOT) / "node_modules").symlink_to(
                    parent / "node_modules", target_is_directory=True
                )
        env = {
            **dbt_environment(),
            "PYTHONPATH": str(copy / "functions/src"),
            "MDP_SCHEMA_ROOT": str(copy / "functions/schemas"),
            "MDP_PG_PASSWORD": "dbt_transform",
        }

        def generate(args):
            return run(args, cwd=copy, env=env)

        generate([*UV, "mdp", "sources", "export"])
        generate([*UV, "mdp", "sources", "doc", "--check"])
        generate([*UV, "python", "ops/analyst-doc.py", "--check"])
        generate([*UV, "python", "ops/ci/generate_sandbox_policy.py", "--check"])
        generate([*UV, "python", "ops/ci/generate_health_policy.py", "--check"])
        if source_registry_changed(paths):
            generate([*UV, "python", "ops/showcase/source_registry.py", "--check"])
        # Both catalogs need this checkout's graph, never an old target/manifest.json.
        shutil.copy2(
            copy / "dbt/profiles/profiles.example.yml",
            copy / "dbt/profiles/profiles.yml",
        )
        generate(
            [
                "uv",
                "run",
                "--project",
                str(ROOT / "dbt"),
                "dbt",
                "parse",
                "--project-dir",
                "dbt",
                "--profiles-dir",
                "dbt/profiles",
                "--target",
                "pg_local",
                "--no-partial-parse",
            ]
        )
        generate([*UV, "python", "ops/label-catalog.py", "--check"])
        if lineage_changed(paths):
            generate([*UV, "python", "ops/showcase/lineage/generate.py", "--check"])
        if sdk_changed(paths):
            generate(
                [
                    "pnpm",
                    "--dir",
                    "control",
                    "--filter",
                    "@mdp/data-sdk",
                    "generate",
                    "--contract-only",
                ]
            )
            for mart in ("mart_chart_history",):
                generate(["pnpm", "--dir", "control", "mdp", "scaffold", "api", mart])
        outputs = [
            "dbt",
            "control/packages/data-sdk/src",
            "control/apps/data-api/src/generated",
        ]
        stale = []
        for output in outputs:
            folder = copy / output
            files = list(folder.rglob("*")) if folder.is_dir() else [folder]
            original = ROOT / output
            originals = list(original.rglob("*")) if original.is_dir() else [original]
            relative = {p.relative_to(copy) for p in files if p.is_file()} | {
                p.relative_to(ROOT) for p in originals if p.is_file()
            }
            for name in relative:
                if any(
                    part in ("target", "logs", ".venv", "dbt_packages", "__pycache__")
                    for part in name.parts
                ) or str(name).startswith("dbt/profiles/"):
                    continue
                a, b = ROOT / name, copy / name
                if not a.exists() or not b.exists() or a.read_bytes() != b.read_bytes():
                    stale.append(str(name))
        if stale:
            raise RuntimeError(
                "Generated files need refreshing:\n"
                + "\n".join(sorted(stale))
                + "\nFollow docs/DEVELOPING.md#before-a-pr, then run bash ops/ready.sh."
            )
        if conformance_changed(paths):
            # The same job runs in CI; its writes stay inside this disposable copy.
            env["MDP_CI_FUNCTIONS_PROJECT"] = str(ROOT / "functions")
            generate(["bash", "ops/ci/jobs/conformance-contracts.sh"])


def test_environment():
    # A sourced local stack must not send disposable tests to its runtime services.
    return {
        key: value for key, value in os.environ.items() if not key.startswith("MDP_")
    }


def dbt_environment():
    # Ignore a sourced stack's scope, profile and output paths in disposable checks.
    return {
        key: value
        for key, value in test_environment().items()
        if not key.startswith("DBT_")
    }


def postgres(action):
    # Docker chooses a free loopback port; this container belongs only to this run.
    container = run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--shm-size=1g",
            "-e",
            "POSTGRES_PASSWORD=postgres",
            "-p",
            "127.0.0.1::5432",
            "postgres:17",
        ]
    ).strip()
    try:
        port = run(["docker", "port", container, "5432"]).strip().rsplit(":", 1)[1]
        env = {
            **test_environment(),
            "MDP_PG_HOST": "127.0.0.1",
            "MDP_PG_PORT": port,
            "MDP_PG_USER": "postgres",
            "POSTGRES_PASSWORD": "postgres",
        }
        for _ in range(60):
            probe = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "postgres",
                ],
                capture_output=True,
                check=False,
            )
            if probe.returncode == 0:
                break
            time.sleep(0.5)
        run(["bash", "ops/local/init.sh"], env=env)
        action(env, port)
    finally:
        # -v also removes the image's anonymous data volume; without it each run leaks one.
        run(["docker", "rm", "-f", "-v", container])


def pytest_checks(paths):
    with DBT_LOCK:
        return _pytest_checks(paths)


def _pytest_checks(paths, tests=None):
    tests = tests or function_tests(paths)
    with tempfile.TemporaryDirectory(prefix="mdp-ready-tests-") as directory:
        report = Path(directory) / "collected.json"
        env = {
            **test_environment(),
            "PYTHONPATH": str(ROOT / "ops"),
            "MDP_READY_COLLECTION": str(report),
            "MDP_CI_DB": str(Path(directory) / "ci.duckdb"),
        }
        run(
            [*UV, "pytest", *tests, "--collect-only", "-q", "-p", "ready_pytest"],
            env=env,
        )
        items = json.loads(report.read_text())
        offline = [node for node, docker in items.items() if not docker]
        docker = [node for node, docker in items.items() if docker]
        if offline:
            run([*UV, "pytest", *offline, "-q"], env=env)
        if docker:

            def check(pg_env, port):
                run(
                    [
                        "bash",
                        "-c",
                        (
                            'source ops/local/init.sh; export MDP_CONTROL_URL="$MDP_FUNCTIONS_RT_DATABASE_URL"; '
                            'export MDP_WAREHOUSE_URL="${MDP_LOADER_WH_DATABASE_URL%/control*}/warehouse?sslmode=prefer"; '
                            'export MDP_SERVICE_READ_URL="${MDP_SERVICE_READ_DATABASE_URL%/control*}/warehouse?sslmode=prefer"; '
                            'exec "$@"'
                        ),
                        "ready",
                        *UV,
                        "pytest",
                        *docker,
                        "-q",
                    ],
                    env={**env, **pg_env, "MDP_CI_DB": env["MDP_CI_DB"]},
                )

            postgres(check)
        return f"{len(offline)} offline, {len(docker)} Postgres tests; " + (
            "fresh database"
            if docker
            else "Postgres skipped: no selected test needs it"
        )


def control_tests(paths):
    packages = sorted(
        {
            "/".join(p.split("/")[1:3])
            for p in paths
            if p.startswith(("control/apps/", "control/packages/"))
        }
    )
    args = [
        "pnpm",
        "--dir",
        "control",
        "exec",
        "vitest",
        "run",
        "--passWithNoTests",
        *packages,
    ]
    if not packages or any(
        p in packages for p in ("apps/control-api", "packages/control-db")
    ):

        def check(env, port):
            env = {
                **env,
                "MDP_TENANTS_TEST_URL": f"postgresql://postgres:postgres@127.0.0.1:{port}/control",
                "MDP_STATUS_TEST_URL": f"postgresql://postgres:postgres@127.0.0.1:{port}/control",
                "MDP_ADMIN_TEST_URL": f"postgresql://migrator:migrator@127.0.0.1:{port}/control",
            }
            run(args, env=env)

        postgres(check)
    else:
        run(args)


def dbt_checks(paths):
    with DBT_LOCK:
        prepare_dbt_profile()
        env = dict(os.environ)
        if (
            any(p.startswith("dbt/models/") for p in paths)
            and not any(p.startswith("ops/ci/") for p in paths)
            and all(
                p.startswith("dbt/models/") or not p.startswith("dbt/") for p in paths
            )
        ):
            env["MDP_LINT_FILES"] = json.dumps(
                [p.removeprefix("dbt/") for p in paths if p.startswith("dbt/")]
            )
        lint = run(["bash", "ops/ci/lint-dbt.sh", "ci"], env=env)
        review = run(["python3", "ops/ci/review_gate.py"], env=env)
        return lint + review + dbt_build(paths)


def dbt_build(paths):
    """Build changed SQL, its descendants and their inputs in a private DuckDB file."""
    with tempfile.TemporaryDirectory(prefix="mdp-ready-dbt-") as directory:
        project = Path(directory) / "dbt"
        shutil.copytree(
            ROOT / "dbt",
            project,
            ignore=shutil.ignore_patterns(".venv", "target", "logs", "dbt_packages"),
        )
        shutil.copy2(
            project / "profiles/profiles.example.yml",
            project / "profiles/profiles.yml",
        )
        env = {
            **dbt_environment(),
            "MDP_CI_DB": str(Path(directory) / "ci.duckdb"),
            "DBT_MDP_SCOPE": "tenant:fixture",
        }
        args = [
            "uv",
            "run",
            "--project",
            str(ROOT / "dbt"),
            "dbt",
            "build",
            "--project-dir",
            str(project),
            "--profiles-dir",
            str(project / "profiles"),
            "--target",
            "ci",
            "--indirect-selection",
            "cautious",
        ]
        changed = [p for p in paths if p.startswith("dbt/") and not p.endswith(".md")]
        if changed and all(
            p.startswith("dbt/models/") and p.endswith(".sql") and (ROOT / p).is_file()
            for p in changed
        ):
            # @ also builds inputs of descendants, so a fresh database has every ref.
            args.extend(
                ["--select", *("@path:" + p.removeprefix("dbt/") for p in changed)]
            )
        # Shared macros, contracts, seeds and deletions need the full graph.
        return run(args, env=env)


def docker_running():
    try:
        return (
            subprocess.run(
                ["docker", "info"], capture_output=True, check=False, timeout=10
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default="main",
        help="branch to compare at its merge base (default: main)",
    )
    parser.add_argument(
        "--list", action="store_true", help="show selected checks without running them"
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="print the unfiltered branch CI gate command without running checks",
    )
    args = parser.parse_args()
    if args.ci:
        branch = run(["git", "branch", "--show-current"]).strip()
        command = shlex.quote(branch) if branch and branch != "main" else "<branch>"
        print(
            "Every SHA needs all five CI workflows and showcase-artifacts-postgres. "
            f"Before merging into main, run: ops/merge-gate.sh {command}"
        )
        return 0
    started = time.monotonic()
    base = run(["git", "merge-base", "HEAD", args.base]).strip()
    paths = changed_paths(base)
    checks = []
    skips = []

    def add(name, action):
        checks.append((name, action))

    def sandbox_boundary():
        with DBT_LOCK:
            return _pytest_checks(
                [],
                tests=[
                    "functions/tests/test_sandbox.py::test_sandbox_owner_team_and_service_boundaries",
                    "functions/tests/test_sandbox_security.py",
                ],
            )

    add("sandbox boundary", sandbox_boundary)
    add("error catalog", lambda: run([*UV, "python", "ops/ci/lint_error_catalog.py"]))
    add("glossary order", lambda: run(["python3", "ops/ci/check_glossary.py"]))
    docs = [p for p in paths if p.endswith(".md")]
    if docs:
        add("docs links", lambda: links(docs))
    else:
        skips.append("docs links: no Markdown changes")
    dbt = any(p.startswith("dbt/") and not p.endswith(".md") for p in paths)
    lint = any(
        p.startswith("ops/ci/")
        and not p.endswith(".md")
        and ("dbt" in p or "review_gate" in p)
        for p in paths
    )
    functions = any(p.startswith("functions/") and not p.endswith(".md") for p in paths)
    control = any(p.startswith("control/") and not p.endswith(".md") for p in paths)
    if dbt or lint:
        add(
            "dbt parse + lint + description guard + review gate + DuckDB build",
            lambda: dbt_checks(paths),
        )
    else:
        skips.append("dbt: no model, contract or lint changes")
    if needs_drift(paths):
        checks.insert(0, ("generated drift", lambda: drift(paths)))
    else:
        skips.append("generated drift: no generator inputs changed")
    if functions:
        add(
            "source network imports",
            lambda: run([*UV, "python", "-m", "mdp_functions.source_lint"]),
        )
        py = [
            p
            for p in paths
            if p.startswith("functions/") and p.endswith(".py") and (ROOT / p).exists()
        ]
        if py:
            add("ruff (touched functions)", lambda: run([*UV, "ruff", "check", *py]))
        add("functions tests (affected imports)", lambda: pytest_checks(paths))
    else:
        skips.append("functions + Postgres: no functions changes")
    if control:
        add("control typecheck", lambda: run(["pnpm", "--dir", "control", "typecheck"]))
        add("control package tests", lambda: control_tests(paths))
    else:
        skips.append("control: no control changes")
    if any(p.startswith("ops/backtest/") and not p.endswith(".md") for p in paths):
        backtest_py = [
            p
            for p in paths
            if p.startswith("ops/backtest/")
            and p.endswith(".py")
            and (ROOT / p).exists()
        ]
        if backtest_py:
            add("backtest lint", lambda: run([*UV, "ruff", "check", *backtest_py]))
        add(
            "backtest tests",
            lambda: run(
                [*UV, "pytest", "-c", "functions/pyproject.toml", "ops/backtest", "-q"]
            ),
        )
    if any(
        (p.startswith("ops/ready") or p == "ops/test_ready.py")
        and not p.endswith(".md")
        for p in paths
    ):
        add(
            "ready selection tests",
            lambda: run(
                [
                    "python3",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "ops",
                    "-p",
                    "test_ready.py",
                ]
            ),
        )
    if any(
        p in ("ops/local/up.sh", "ops/local/common.sh", "ops/local/test_up_tools.py")
        for p in paths
    ):
        add(
            "local tool check",
            lambda: run(
                [
                    "python3",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "ops/local",
                    "-p",
                    "test_up_tools.py",
                ]
            ),
        )
    analysis = any(p.startswith(("analyses/", "r/mdpr/inst/templates/")) for p in paths)
    guard = analysis or any(
        p in ("ops/ci/analyses_guard.py", "ops/ci/test_analyses_guard.py")
        for p in paths
    )
    if guard:
        add("analyses guard", lambda: run(["python3", "ops/ci/analyses_guard.py"]))
        add(
            "analyses guard tests",
            lambda: run(
                [
                    "python3",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "ops/ci",
                    "-p",
                    "test_analyses_guard.py",
                ]
            ),
        )
    if any(p.startswith(("r/", "analyses/", "ops/r/")) for p in paths):
        if docker_running():
            add("R checks (Docker)", lambda: run(["bash", "ops/r/check.sh", "--quick"]))
        else:
            skips.append("R checks (Docker): Docker not running; r-ci runs it")
    covered = (
        "ops/local/up.sh",
        "ops/local/common.sh",
        "ops/local/test_up_tools.py",
        "r/",
        "analyses/",
        "ops/r/",
        "ops/ci/analyses_guard.py",
        "ops/ci/test_analyses_guard.py",
        "ops/ci/check_glossary.py",
        "ops/ci/generate_health_policy.py",
        "ops/ci/jobs/conformance-contracts.sh",
        ".github/workflows/conformance.yml",
        "ops/showcase/lineage/",
        "ops/showcase/source_registry.py",
        "ops/showcase/queries.json",
        "docs/",
        "dbt/",
        "functions/",
        "control/",
        "ops/ready",
        "ops/backtest/",
        "ops/test_ready.py",
        "ops/evidence/",
    )
    unknown = [
        p
        for p in paths
        if not p.endswith(".md")
        and not p.startswith(covered)
        and not (p.startswith("ops/ci/") and ("dbt" in p or "review_gate" in p))
    ]
    if unknown:
        skips.append("CI-only paths (full CI required): " + ", ".join(unknown))
    if args.list:
        print("ready: " + str(len(paths)) + " changed paths since " + base[:8])
        for name, _ in checks:
            print("RUN " + name)
        for skip in skips:
            print("SKIP " + skip)
        return 0

    def execute(check):
        name, action = check
        start = time.monotonic()
        try:
            detail = action()
            return (
                name,
                time.monotonic() - start,
                None,
                detail if name.startswith("functions tests") else None,
            )
        except (RuntimeError, OSError, ValueError, SyntaxError) as error:
            return name, time.monotonic() - start, str(error), None

    results = []
    if checks and checks[0][0] == "generated drift":
        results.append(execute(checks.pop(0)))
        if results[0][2]:
            skips.extend(
                name + ": refresh generated files, then run bash ops/ready.sh"
                for name, _ in checks
            )
            checks = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        results.extend(pool.map(execute, checks))
    failed = sum(error is not None for _, _, error, _ in results)
    print(
        f"ready: {len(results) - failed} passed, {failed} failed in {time.monotonic() - started:.1f}s ({len(paths)} changed paths)"
    )
    for name, elapsed, error, detail in results:
        print(
            f"  {'FAIL' if error else 'PASS'} {name} ({elapsed:.1f}s)"
            + (f" · {detail}" if detail else "")
        )
        if error:
            print(error)
    for skip in skips:
        print("  SKIP " + skip)
    print(
        "Next: fix failed checks and run bash ops/ready.sh, or commit and run ops/merge-gate.sh <branch>."
    )
    return int(bool(failed))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError) as error:
        print(f"ready: {error}", file=sys.stderr)
        sys.exit(1)
