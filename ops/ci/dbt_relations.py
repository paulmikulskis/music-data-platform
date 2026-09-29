"""Require source()/ref() for schema-qualified reads in dbt model SQL."""

import json
import os
import re
import sys
from pathlib import Path

import sqlparse
from sqlparse.sql import Function, Identifier, IdentifierList, TokenList
from sqlparse.tokens import DML, Comment, Keyword

# The standalone lint uses dbt's environment; the policy has no runtime dependencies.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "functions/src"))
from mdp_functions.sandbox_policy import POLICY


def literal_reads(sql: str) -> list[str]:
    # Keep Jinja relation expressions as unqualified placeholders; ignore config,
    # macro arguments and comments, which may legitimately name raw tables.
    sql = re.sub(r"\{\{.*?\}\}", "__dbt_expression__", sql, flags=re.DOTALL)
    sql = re.sub(r"\{%.*?%\}|\{#.*?#\}", " ", sql, flags=re.DOTALL)
    sql = sqlparse.format(sql, strip_comments=True)
    reads = []

    def walk(group: TokenList) -> None:
        query = any(
            token.ttype in DML and token.normalized == "SELECT"
            for token in group.tokens
        )
        for index, token in enumerate(group.tokens):
            # EXTRACT/substring/trim use FROM before a column, not a relation.
            if (
                (query or not isinstance(group.parent, Function))
                and token.ttype in Keyword
                and (token.normalized == "FROM" or token.normalized.endswith("JOIN"))
            ):
                position, relation = group.token_next(index)
                while (
                    relation is not None
                    and relation.ttype in Keyword
                    and relation.normalized in ("ONLY", "LATERAL")
                ):
                    position, relation = group.token_next(position)
                relations = (
                    relation.get_identifiers()
                    if isinstance(relation, IdentifierList)
                    else [relation]
                )
                for item in relations:
                    if (
                        isinstance(item, Identifier)
                        and item.get_parent_name() is not None
                    ):
                        reads.append(str(item))
            if isinstance(token, TokenList):
                walk(token)

    for statement in sqlparse.parse(sql):
        walk(statement)
    return reads


def sandbox_reads(project: Path) -> list[str]:
    """Sandboxes cannot enter production through SQL, a macro or source metadata."""
    bad = []
    for folder in ("models", "macros"):
        for path in sorted((project / folder).rglob("*")):
            if path.suffix not in {".sql", ".yml", ".yaml"}:
                continue
            content = re.sub(r"\{#.*?#\}", "", path.read_text(), flags=re.DOTALL)
            if path.suffix == ".sql":
                content = "".join(value for kind, value in sqlparse.lexer.tokenize(content) if kind not in Comment)
            else:
                content = re.sub(r"(?m)^\s*#.*$", "", content)
            if re.search(r"\b" + re.escape(POLICY["prefix"]), content, re.IGNORECASE):
                bad.append(f"{path.relative_to(project)}: sandbox relations cannot feed dbt; lift the view first")
    return bad


def main(project: Path) -> int:
    bad = sandbox_reads(project)
    touched = json.loads(os.environ.get("MDP_LINT_FILES", "null"))
    paths = (
        [project / p for p in touched if p.endswith(".sql")]
        if touched is not None
        else (project / "models").rglob("*.sql")
    )
    for path in sorted(p for p in paths if p.is_file()):
        for relation in literal_reads(path.read_text()):
            bad.append(
                f"{path.relative_to(project)}: literal relation {relation}; use source()/ref()"
            )
    if bad:
        print("\n".join(bad))
        return 1
    print("PASS model relations: no literal schema-qualified reads")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
