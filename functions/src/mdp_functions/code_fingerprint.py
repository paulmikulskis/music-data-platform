"""Hash a source and its local Python dependencies for deploy canaries."""

import ast
import hashlib
from functools import cache

from mdp_functions.settings import PACKAGE


@cache
def fingerprint(function):
    pending = [function.__module__]
    seen = {}
    while pending:
        module = pending.pop()
        if module in seen or not module.startswith("mdp_functions"):
            continue
        path = PACKAGE.parent.joinpath(*module.split(".")).with_suffix(".py")
        if not path.exists():
            path = path.with_suffix("") / "__init__.py"
        if not path.exists():
            continue
        body = path.read_bytes()
        seen[module] = body
        for node in ast.walk(ast.parse(body)):
            if isinstance(node, ast.Import):
                pending.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                pending.append(node.module)
                pending.extend(node.module + "." + alias.name for alias in node.names)
    digest = hashlib.sha256()
    for module, body in sorted(seen.items()):
        digest.update(module.encode() + b"\0" + body)
    return digest.hexdigest()
