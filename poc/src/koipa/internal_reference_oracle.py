"""Second ledger oracle: CSV parsing and in-memory SQL, no generator/primary imports.

This checks a finite fictional language, not arbitrary document meaning or real
identity/anonymization. The policy parameter is supplied explicitly by the caller.
"""
from __future__ import annotations

import csv
import io
import sqlite3


def sql_oracle(text: str, *, threshold: int, released: bool | None) -> dict:
    tables = {}
    lines = text.splitlines()
    markers = {"[연결표]", "[지급표]", "[배정표]"}
    for pos, line in enumerate(lines):
        if line not in markers:
            continue
        stop = next(i for i in range(pos + 1, len(lines)) if lines[i] in markers or lines[i] == "[끝]")
        parsed = list(csv.DictReader(io.StringIO("\n".join(lines[pos + 1:stop])), delimiter="|"))
        tables[line] = parsed
    with sqlite3.connect(":memory:") as conn:
        conn.executescript("CREATE TABLE identities(k TEXT PRIMARY KEY, person TEXT);"
                           "CREATE TABLE payments(tx TEXT PRIMARY KEY, recipient TEXT, amount INTEGER);")
        conn.executemany("INSERT INTO identities VALUES (?, ?)",
                         [(r["연결키"], r["인물ID"]) for r in tables["[연결표]"]])
        conn.executemany("INSERT INTO payments VALUES (?, ?, ?)",
                         [(r["처리키"], r["연결키"], int(r["금액단위"])) for r in tables["[지급표]"]])
        n = conn.execute("SELECT COUNT(DISTINCT i.person) FROM identities i JOIN payments p ON i.k = p.recipient").fetchone()[0]
        outcomes = conn.execute(
            "WITH releases(v) AS (VALUES (0),(1)) SELECT DISTINCT CASE WHEN ? >= ? THEN 'TS' "
            "WHEN ? > 0 THEN 'S1' WHEN v=1 THEN 'S3' ELSE 'S2' END FROM releases WHERE ? IS NULL OR v=?",
            (n, threshold, n, released, released),
        ).fetchall()
    return {"linked_unique_people": n, "possible_grades": sorted(r[0] for r in outcomes)}
