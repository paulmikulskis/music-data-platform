"""Publish only a successfully reviewed draft, in a disposable checkout."""

import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from mdp_functions.errors import ServiceError
from mdp_functions.settings import REPO


def prepare_files(checkout, draft, columns):
    """Merge the existing model properties, preserving tests, config and metadata."""
    models = checkout / "dbt/models"
    matches = list(models.rglob(draft["model"] + ".sql"))
    if len(matches) > 1:
        raise ServiceError("workbench_pr_failed", "Model has multiple SQL paths")
    path = matches[0] if matches else models / "marts" / (draft["model"] + ".sql")
    properties = []
    for candidate in sorted(models.rglob("*")):
        if candidate.suffix not in {".yml", ".yaml"}:
            continue
        document = yaml.safe_load(candidate.read_text()) or {}
        for entry in document.get("models", []):
            if entry.get("name") == draft["model"]:
                properties.append((candidate, document, entry))
    if len(properties) > 1:
        raise ServiceError("workbench_pr_failed", "Model has duplicate YAML entries")
    if properties:
        properties_path, document, entry = properties[0]
    else:
        properties_path = models / "marts/_marts__models.yml"
        document = (
            yaml.safe_load(properties_path.read_text())
            if properties_path.exists()
            else {"version": 2, "models": []}
        )
        entry = {"name": draft["model"], "config": {"contract": {"enforced": False}}}
        document.setdefault("models", []).append(entry)
    existing = {c["name"]: c for c in entry.setdefault("columns", [])}
    for column in columns:
        if column["name"] in existing:
            existing[column["name"]]["data_type"] = column["type"]
        else:
            entry["columns"].append(
                {"name": column["name"], "data_type": column["type"]}
            )
    # The reviewed output is the contract: a column the draft no longer produces leaves
    # the declaration (with its tests), so an enforced contract cannot fail on a column
    # the SQL does not select. The unified diff carries the removal for the PR reviewer.
    produced = {column["name"] for column in columns}
    entry["columns"] = [c for c in entry["columns"] if c["name"] in produced]
    files = {
        path: draft["sql"],
        properties_path: yaml.safe_dump(document, sort_keys=False),
    }
    patches = []
    for destination, content in files.items():
        relative = destination.relative_to(checkout).as_posix()
        patches.extend(
            difflib.unified_diff(
                destination.read_text().splitlines(True)
                if destination.exists()
                else [],
                content.splitlines(True),
                fromfile=f"a/{relative}" if destination.exists() else "/dev/null",
                tofile=f"b/{relative}",
            )
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
    # Parse the serialized properties too, before invoking dbt on the complete project.
    yaml.safe_load(properties_path.read_text())
    return {
        p.relative_to(checkout).as_posix(): content for p, content in files.items()
    }, "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        for line in patches
    )


def validate_project(checkout, run):
    # Parsing needs no warehouse connection or credentials. Keep all artifacts in the clone.
    profiles = checkout / ".workbench-profiles"
    profiles.mkdir()
    (profiles / "profiles.yml").write_text(
        yaml.safe_dump(
            {
                "music_data_platform": {
                    "target": "workbench",
                    "outputs": {
                        "workbench": {
                            "type": "duckdb",
                            "path": ":memory:",
                            "threads": 1,
                        }
                    },
                }
            }
        )
    )
    run(
        [
            "uv",
            "run",
            "--no-sync",
            "--offline",
            "--python",
            str(REPO / "dbt/.venv/bin/python"),
            "--project",
            str(checkout / "dbt"),
            "dbt",
            "parse",
            "--no-partial-parse",
            "--project-dir",
            str(checkout / "dbt"),
            "--profiles-dir",
            str(profiles),
            "--target",
            "workbench",
            "--target-path",
            str(checkout / ".workbench-target"),
            "--log-path",
            str(checkout / ".workbench-logs"),
            "--vars",
            json.dumps({"wb_schema": "wb_pr_validation"}),
        ],
        checkout,
    )


def publish(session, draft, columns, *, dry_run=False):
    if not re.fullmatch("[a-z][a-z0-9_]*", draft["model"]):
        raise ServiceError("workbench_pr_failed", "Invalid model name")
    branch = f"workbench/{str(session['id'])[:8]}-{draft['model']}"
    result = {
        "branch": branch,
        "diff": "",
        "pr_opened": False,
        "url": None,
        "message": "No pull request opened: GitHub access is not set. Apply the patch in your clone.",
    }
    env = dict(os.environ)
    # The explicit Python path fails if dbt is missing, without creating an environment.
    # Reuse installed dbt without syncing dependencies or writing bytecode into it.
    env["UV_PROJECT_ENVIRONMENT"] = str(REPO / "dbt/.venv")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env.get("GITHUB_TOKEN"):
        env["GH_TOKEN"] = env["GITHUB_TOKEN"]
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "credential.helper"
        env["GIT_CONFIG_VALUE_0"] = "!gh auth git-credential"
    without_auth = not dry_run and (
        not shutil.which("gh")
        or subprocess.run(
            ["gh", "auth", "status"], env=env, capture_output=True, check=False
        ).returncode
    )

    def run(args, cwd):
        command = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        if command.returncode:
            # Remote errors can contain credentials or identifying local paths.
            raise ServiceError(
                "workbench_pr_failed",
                "Repository operation failed: " + args[0] + " " + args[1],
            )
        return command.stdout.strip()

    with tempfile.TemporaryDirectory(prefix="mdp-pr-") as temporary:
        root = Path(temporary)
        if dry_run or without_auth:
            checkout = root / "draft"
            shutil.copytree(
                REPO / "dbt",
                checkout / "dbt",
                ignore=shutil.ignore_patterns(
                    ".venv", "target", "logs", ".git", "__pycache__"
                ),
            )
            files, patch = prepare_files(checkout, draft, columns)
            validate_project(checkout, run)
            return {
                **result,
                "diff": patch,
                "files": files,
                "message": (
                    "Dry run: generated files passed YAML and dbt parse validation."
                    if dry_run
                    else result["message"]
                ),
            }
        clone = root / "repository"
        remote = (
            "https://github.com/" + env["GITHUB_REPO"] + ".git"
            if env.get("GITHUB_TOKEN")
            else run(["git", "remote", "get-url", "origin"], REPO)
        )
        run(["git", "clone", "--no-checkout", remote, str(clone)], root)
        checkout = root / "draft"
        run(
            ["git", "worktree", "add", "-b", branch, str(checkout), "origin/main"],
            clone,
        )
        files, patch = prepare_files(checkout, draft, columns)
        result["diff"] = patch
        validate_project(checkout, run)
        run(["git", "add", *files], checkout)
        run(
            [
                "git",
                "-c",
                "user.name=Workbench",
                "-c",
                "user.email=" + env.get("MDP_GIT_AUTHOR_EMAIL", "workbench@users.noreply.github.com"),
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-m",
                "Add reviewed workbench draft",
            ],
            checkout,
        )
        run(["git", "push", "origin", branch], checkout)
        body = root / "pr.md"
        body.write_text(
            "Adds the SQL draft and column contract reviewed in the workbench preview.\n\nValidation: this exact SQL completed a session-isolated dbt build.\n"
        )
        url = run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                env["GITHUB_REPO"],
                "--base",
                "main",
                "--head",
                branch,
                "--title",
                "Workbench draft: " + draft["model"],
                "--label",
                "workbench",
                "--body-file",
                str(body),
            ],
            checkout,
        )
        return {
            **result,
            "pr_opened": True,
            "url": url,
            "message": "Opened the reviewed draft for repository review.",
        }
