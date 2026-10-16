"""Deterministic query results: DuckDB returns rows in no fixed order unless the query orders them."""
from __future__ import annotations

import json

import duckdb


def ordered(con, sql: str) -> str:
    """The query with a total row order, so that gold answers do not depend on DuckDB's thread scheduling.

    Without an outer ORDER BY, DuckDB returns groups and joins in an order that changes from run to run; such queries
    are sorted by all columns. With an ORDER BY, rows that tie on its keys also come back in any order (and LIMIT then
    picks any of them), so every output column is appended as a final tie-break."""
    try:
        tree = json.loads(con.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0])
    except duckdb.Error:
        return sql
    if tree.get("error") or len(tree["statements"]) != 1:
        return sql
    mods = tree["statements"][0]["node"].get("modifiers", [])
    order = next((m for m in mods if m["type"] == "ORDER_MODIFIER"), None)
    if order is None:
        if any(m["type"] == "LIMIT_MODIFIER" for m in mods):
            raise ValueError(f"LIMIT without ORDER BY picks arbitrary rows: {' '.join(sql.split())[:200]}")
        return f"SELECT * FROM ({sql}) ORDER BY ALL"
    n = len(con.execute(f"DESCRIBE {sql}").fetchall())
    order["orders"] += [{"type": "ASCENDING", "null_order": "NULLS_LAST", "expression": {
        "class": "CONSTANT", "type": "VALUE_CONSTANT", "alias": "", "query_location": 0,
        "value": {"type": {"id": "INTEGER", "type_info": None}, "is_null": False, "value": k}}} for k in range(1, n + 1)]
    return con.execute("SELECT json_deserialize_sql(?::JSON)", [json.dumps(tree)]).fetchone()[0]
