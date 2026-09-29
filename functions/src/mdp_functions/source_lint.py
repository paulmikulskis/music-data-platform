"""Source authors use ctx.http; URL parsing remains a pure local operation."""

import ast
from pathlib import Path

from mdp_functions.fetch.guard import DOC
from mdp_functions.settings import PACKAGE

NETWORK_MODULES = {
    "httpx",
    "httpcore",
    "requests",
    "aiohttp",
    "urllib",
    "urllib3",
    "socket",
    "_socket",
    "playwright",
    "selenium",
    "pyppeteer",
    "mechanize",
    "httplib2",
    "grpc",
    "curl_cffi",
    "pycurl",
    "curl",
    "http",
    "httplib",
    "websocket",
    "websockets",
    "ftplib",
    "telnetlib",
    "openai",
    "anthropic",
    "litellm",
}


def check(root: Path = PACKAGE / "sources") -> list[str]:
    problems = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                if node.module in {"http", "urllib"}:
                    names = [f"{node.module}.{alias.name}" for alias in node.names]
            else:
                continue
            for name in names:
                if name == "urllib.parse":
                    continue
                if any(
                    name == banned or name.startswith(banned + ".")
                    for banned in NETWORK_MODULES
                ):
                    problems.append(
                        f"{path}:{node.lineno}: import {name} is refused because own clients bypass "
                        f"request lineage; use ctx.http ({DOC})."
                    )
    return problems


def main():
    problems = check()
    print("\n".join(problems) if problems else "Source network imports: pass")
    return bool(problems)


if __name__ == "__main__":
    raise SystemExit(main())
