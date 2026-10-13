"""SQL is written by the code, and every value goes in as a parameter.

A statement or a piece of one built at run time is the one place a value could become
SQL. Each is listed here with what it puts into the text, and every piece was read: a
constant of its module, a number, a key as hex, an identifier quoted by `_quoted`, a
run of `?` placeholders, or a fixed condition its own callers pass. A new one, or a new
piece in an old one, fails until someone reads it and adds it. Building one with `%`
or `.format()` always fails.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

# Text that opens a statement or a clause, as this code writes SQL: in capitals.
_SQL = re.compile(
    r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|PRAGMA|WITH|ATTACH|DETACH|REPLACE"
    r"|WHERE|AND|OR|ORDER|LIMIT)\b"
)

# (file, function) -> every piece its built SQL interpolates, each one read.
REVIEWED: dict[tuple[str, str], frozenset[str]] = {
    # `where`: `_filtered`, fixed clauses with `?`.
    ("kernel/audit.py", "list"): frozenset({"AS_READ", "where"}),
    ("kernel/audit.py", "count"): frozenset({"where"}),
    ("kernel/audit.py", "_filtered"): frozenset({"' AND '.join(clauses)"}),
    ("kernel/audit_integrity.py", "_read"): frozenset({"AS_READ"}),
    # The key as hex, a version as a number, a table name read from the file, quoted.
    ("services/memory/at_rest.py", "_encrypt"): frozenset({"key.hex()", "int(version)"}),
    ("services/memory/at_rest.py", "_rows"): frozenset({"_quoted(name)"}),
    ("services/memory/cipher.py", "open_sync"): frozenset({"key.hex()"}),
    ("services/memory/migrations.py", "apply_pending"): frozenset({"int(step.version)"}),
    ("services/memory/prune_store.py", "count_prunable"): frozenset({"_PRUNABLE"}),
    ("services/memory/prune_store.py", "prune_oldest"): frozenset({"_PRUNABLE"}),
    # The embedding's size, an int from the settings.
    ("services/memory/rag_store.py", "init_schema"): frozenset({"self._dim"}),
    # `scope`: `_scope`, a fixed column and `?` per document.
    ("services/memory/rag_store.py", "_scope"): frozenset({"column", "placeholders"}),
    ("services/memory/rag_store.py", "knn_chunks"): frozenset({"_CHUNK_COLUMNS", "scope"}),
    ("services/memory/rag_store.py", "keyword_chunks"): frozenset({"_CHUNK_COLUMNS", "scope"}),
    # `which`: one of the fixed conditions its callers in the same class pass.
    ("services/memory/recent_store.py", "_contents"): frozenset({"which"}),
    ("services/memory/store.py", "_same_content"): frozenset({"which"}),
    # Table, column and declaration: constants at every call (`ensure_column(db, "...")`).
    ("services/memory/schema.py", "ensure_column"): frozenset({"table", "column", "decl"}),
    ("services/memory/schema.py", "open_database"): frozenset({"embedding_dim"}),
    ("services/memory/store.py", "list_memories"): frozenset({"' AND '.join(clauses)", "where"}),
    ("services/memory/store.py", "bump_access"): frozenset({"marks"}),
    # `likes`: "normalized_content LIKE ?" once per word.
    ("services/memory/vector_store.py", "lexical_candidates"): frozenset({"likes"}),
}


def _text(node: ast.expr) -> str | None:
    """The literal text of a string constant or an f-string's fixed parts."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    return None


def _constant(node: ast.expr) -> bool:
    """A string literal, or a name the module spells as a constant."""
    if isinstance(node, ast.Constant):
        return True
    name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")
    return bool(name) and name.lstrip("_").isupper()


def _operands(node: ast.expr) -> Iterator[ast.expr]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        yield from _operands(node.left)
        yield from _operands(node.right)
    else:
        yield node


def built_sql(tree: ast.AST) -> Iterator[tuple[str, frozenset[str] | None]]:
    """(function, the pieces it interpolates) for each piece of SQL built at run time;
    the pieces are None for one built with `%` or `.format()`."""
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(func):
            if isinstance(node, ast.JoinedStr) and _SQL.match(_text(node) or ""):
                pieces = (
                    ast.unparse(part.value)
                    for part in node.values
                    if isinstance(part, ast.FormattedValue)
                )
                yield func.name, frozenset(pieces)
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                operands = list(_operands(node))
                if any(_SQL.match(_text(o) or "") for o in operands):
                    loose = [ast.unparse(o) for o in operands if not _constant(o)]
                    if loose:
                        yield func.name, frozenset(loose)
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
                if _SQL.match(_text(node.left) or ""):
                    yield func.name, None
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "format"
                and _SQL.match(_text(node.func.value) or "")
            ):
                yield func.name, None


def _sites() -> dict[tuple[str, str], set[str]]:
    found: dict[tuple[str, str], set[str]] = {}
    for path in sorted(APP.rglob("*.py")):
        relative = path.relative_to(APP).as_posix()
        for function, pieces in built_sql(ast.parse(path.read_text(encoding="utf-8"))):
            key = (relative, function)
            found.setdefault(key, set()).update(pieces or {"<% or .format()>"})
    return found


def test_every_piece_of_sql_built_at_run_time_has_been_read() -> None:
    unread = {
        key: sorted(pieces - REVIEWED.get(key, frozenset()))
        for key, pieces in _sites().items()
        if pieces - REVIEWED.get(key, frozenset())
    }

    assert unread == {}


def test_the_list_holds_nothing_that_no_longer_exists() -> None:
    """A site that changed or went must leave the list, or it would pass unread later."""
    found = _sites()

    stale = {key for key, pieces in REVIEWED.items() if found.get(key) != set(pieces)}

    assert stale == set()


def _check(source: str) -> list[tuple[str, frozenset[str] | None]]:
    return list(built_sql(ast.parse(source)))


def test_it_finds_a_value_put_into_sql_however_it_is_written() -> None:
    """The check itself (mutation: miss a form)."""
    assert _check("def a(n):\n    db.execute(f\"SELECT * FROM t WHERE name = '{n}'\")") == [
        ("a", frozenset({"n"}))
    ]
    assert _check('def b(n):\n    sql = "DELETE FROM t WHERE id = " + n') == [
        ("b", frozenset({"n"}))
    ]
    assert _check('def c(n):\n    db.execute("SELECT %s" % n)') == [("c", None)]
    assert _check('def d(n):\n    db.execute("UPDATE t SET x = {}".format(n))') == [("d", None)]
    assert _check('def e(n):\n    db.execute("SELECT x FROM t WHERE id = ?", (n,))') == []
    assert _check('def f(n):\n    say(f"Selected {n} files")') == []
