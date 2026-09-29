"""Require recovery guidance for named errors and check generated hints."""

import ast
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "error_catalog_generator",
    ROOT / "control/packages/contracts/scripts/error-catalog.py",
)
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)
CODE = re.compile(r"^[a-z][a-z0-9_]*$")


def strings(node, definitions=None, seen=frozenset()):
    if isinstance(node, ast.Subscript):
        # A lookup's key is not the error code returned by the lookup.
        return strings(node.value, definitions, seen)
    if isinstance(node, ast.Constant):
        value = node.value
        return (
            {value}
            if isinstance(value, str)
            and CODE.fullmatch(value)
            and value != "error_class"
            else set()
        )
    if isinstance(node, ast.IfExp):
        return strings(node.body, definitions, seen) | strings(
            node.orelse, definitions, seen
        )
    if isinstance(node, ast.Name):
        result = set()
        if definitions and node.id not in seen:
            for value in definitions.get(node.id, []):
                result.update(strings(value, definitions, seen | {node.id}))
        return result
    result = set()
    for child in ast.iter_child_nodes(node):
        result.update(strings(child, definitions, seen))
    return result


def error_codes(root=ROOT):
    found = {}

    def add(codes, path):
        for code in codes:
            found.setdefault(code, set()).add(str(path.relative_to(root)))

    for folder in ("functions/src", "ops"):
        for path in (root / folder).rglob("*.py"):
            if "evidence" in path.parts or path.name.startswith("test_"):
                continue
            source = path.read_text()
            add(re.findall(r"error_class\s*=\s*'([a-z][a-z0-9_]*)'", source), path)
            tree = ast.parse(source)
            owners = {}
            for function in ast.walk(tree):
                if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for child in ast.walk(function):
                        owners[id(child)] = function
            assignments = {}
            for node in ast.walk(tree):
                scope = owners.get(id(node), tree)
                definitions = assignments.setdefault(id(scope), {})
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value:
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    for target in targets:
                        if isinstance(target, ast.Name):
                            definitions.setdefault(target.id, []).append(node.value)
            for node in ast.walk(tree):
                definitions = {
                    **assignments.get(id(tree), {}),
                    **assignments.get(id(owners.get(id(node), tree)), {}),
                }
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and "runbook_slug" in node.value
                ):
                    add(
                        (
                            slug.replace("-", "_")
                            for slug in re.findall(
                                r"'([a-z]+(?:-[a-z0-9]+)+)'", node.value
                            )
                        ),
                        path,
                    )
                if (
                    isinstance(node, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == "RUNBOOKS"
                        for t in node.targets
                    )
                    and isinstance(node.value, ast.Dict)
                ):
                    for key in node.value.keys:
                        if key is not None:
                            add(strings(key), path)
                if isinstance(node, ast.Call):
                    name = ast.unparse(node.func)
                    index = {"ServiceError": 0, "alert": 2, "reject": 2}.get(
                        name.split(".")[-1]
                    )
                    if index is not None and len(node.args) > index:
                        add(strings(node.args[index], definitions), path)
                    for keyword in node.keywords:
                        if keyword.arg == "error_class":
                            add(strings(keyword.value, definitions), path)
                if isinstance(node, ast.Dict):
                    for key, value in zip(node.keys, node.values):
                        if (
                            isinstance(key, ast.Constant)
                            and key.value == "error_class"
                            and not isinstance(value, ast.BinOp)
                        ):
                            add(strings(value, definitions), path)
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    if (
                        any(
                            ast.unparse(t).split(".")[-1] == "error_class"
                            for t in targets
                        )
                        and node.value
                    ):
                        add(strings(node.value, definitions), path)
            for cls in (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.ClassDef)
                and any(ast.unparse(b).endswith("ServiceError") for b in n.bases)
            ):
                for node in ast.walk(cls):
                    if (
                        isinstance(node, ast.Call)
                        and ast.unparse(node.func) == "super().__init__"
                        and node.args
                    ):
                        add(strings(node.args[0]), path)
    for path in (root / "control").rglob("*.ts*"):
        if (
            any(p in path.parts for p in ("node_modules", "test", "generated"))
            or path.name == "error-catalog.ts"
        ):
            continue
        source = path.read_text()
        for match in re.finditer(
            r'(?:new AppError|dataError)\(\s*([\s\S]*?),\s*(?:["`\']|\w)', source
        ):
            add(re.findall(r'["\']([a-z][a-z0-9_]*)["\']', match[1]), path)
        add(
            re.findall(r'error_class\s*[:=]\s*["\']([a-z][a-z0-9_]*)["\']', source),
            path,
        )
    for path in (root / "dbt/macros").rglob("*.sql"):
        add(
            re.findall(
                r'raise_compiler_error\(\s*["\']([a-z][a-z0-9_]*):', path.read_text()
            ),
            path,
        )
    return found


# These developer-only messages are prose, so require an instruction rather than a new code.
NEXT_VERB = re.compile(
    r"\b(run|rerun|set|use|see|pass|open|choose|select|check|read|add|ask|install|pull|retry|reconnect|filter|re-land|declare|inspect|reduce|copy)\b",
    re.IGNORECASE,
)


def message_arguments(source, pattern):
    for match in re.finditer(pattern, source):
        start = match.end()
        depth, quote, escaped = 1, None, False
        for end in range(start, len(source)):
            char = source[end]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    yield source[start:end], source.count("\n", 0, match.start()) + 1
                    break


def prose_errors(root=ROOT, rows=None):
    rows = rows or {}
    missing = []
    for folder, suffix, pattern in (
        ("dbt/macros", "*.sql", r"raise_compiler_error\s*\("),
        ("r/mdpr/R", "*.R", r"\bstop\s*\("),
    ):
        for path in (root / folder).rglob(suffix):
            for message, line in message_arguments(path.read_text(), pattern):
                codes = re.findall(r'sandbox_message\("([a-z][a-z0-9_]*)"\)', message)
                if codes and all(
                    code in rows and NEXT_VERB.search(rows[code]["next_step"])
                    for code in codes
                ):
                    continue
                if not NEXT_VERB.search(message):
                    missing.append(f"{path.relative_to(root)}:{line}")
    path = root / "ops/ci/lint_dbt.py"
    if path.exists():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and ast.unparse(node.func) == "SystemExit"
                and node.args
            ):
                message = node.args[0]
                if isinstance(message, ast.Constant) and isinstance(message.value, int):
                    continue
                if not NEXT_VERB.search(ast.unparse(message)):
                    missing.append(f"ops/ci/lint_dbt.py:{node.lineno}")
    return missing


def main():
    try:
        rows = generator.read_catalog()
        missing = {k: sorted(v) for k, v in error_codes().items() if k not in rows}
        if missing:
            raise ValueError("Add catalog rows with next_step: " + repr(missing))
        prose = prose_errors(rows=rows)
        if prose:
            raise ValueError("Add a next-step instruction at " + ", ".join(prose))
        generator.generate(check=True)
    except ValueError as error:
        raise SystemExit(f"FAIL error catalog: {error}") from error
    print(
        f"PASS error catalog: {len(rows)} recovery hints; source coverage and generated files current"
    )


if __name__ == "__main__":
    main()
