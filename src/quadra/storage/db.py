"""Thin DuckDB helpers: connect, run a multi-statement SQL script, query."""
from __future__ import annotations

import pathlib

import duckdb


def connect(db_path: str) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(db_path)


def configure(con: duckdb.DuckDBPyConnection, *, memory_limit: str | None = None,
              threads: int | None = None, temp_dir: str | None = None) -> None:
    """Bound DuckDB's resource use so a big load spills to disk instead of OOM-killing the box.

    DuckDB defaults to ~80% of RAM and one thread per core; on a many-core machine that peak,
    times many threads each holding JSON scratch, overran RAM+swap during the 2.52M-row feed-in
    load. A modest memory_limit + fewer threads + an on-disk temp_directory keeps it safe.
    """
    if memory_limit:
        con.execute(f"SET memory_limit='{memory_limit}'")
    if threads:
        con.execute(f"SET threads={int(threads)}")
    if temp_dir:
        pathlib.Path(temp_dir).mkdir(parents=True, exist_ok=True)
        con.execute(f"SET temp_directory='{temp_dir}'")
        con.execute("SET preserve_insertion_order=false")  # lower peak memory for big INSERTs


def _strip_line_comments(text: str) -> str:
    """Remove `--` to end-of-line on every line. The project's SQL never contains `--`
    inside a string literal or identifier, so this is safe and lets comments carry ';'."""
    stripped = []
    for line in text.splitlines():
        idx = line.find("--")
        stripped.append(line if idx == -1 else line[:idx])
    return "\n".join(stripped)


def _split_statements(text: str) -> list[str]:
    """Split a SQL script into statements on ';' (after removing comments).

    Comments are stripped first so a ';' inside a comment cannot break statement splitting.
    The remaining SQL contains no ';' inside string literals or identifiers (JSON paths use
    '$.a.b', enum sets use quoted words), so a plain split is safe and keeps the files readable.
    """
    return [s.strip() for s in _strip_line_comments(text).split(";") if s.strip()]


def run_sql_script(con: duckdb.DuckDBPyConnection, text: str) -> None:
    for stmt in _split_statements(text):
        con.execute(stmt)


def run_sql_file(con: duckdb.DuckDBPyConnection, path: str | pathlib.Path) -> None:
    run_sql_script(con, pathlib.Path(path).read_text())


def query(con: duckdb.DuckDBPyConnection, sql: str, params=None):
    return con.execute(sql, params or []).fetchall()
