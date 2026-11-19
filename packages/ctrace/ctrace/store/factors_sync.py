"""Mirror the code-defined emission factors (ctrace.factors) into the `emission_factors` table.

Factor rows are immutable once referenced by the ledger: a changed value must get a new version
(and therefore a new id). Re-syncing an existing id with a different value is refused.
"""
from __future__ import annotations

import json

from ..factors import all_factors
from .db import Conn


class FactorConflict(RuntimeError):
    pass


def sync_factors(conn: Conn) -> int:
    n = 0
    with conn.transaction():
        for f in all_factors():
            row = conn.execute("select kgco2e_per_unit from emission_factors where id = %s", (f.factor_id,)).fetchone()
            if row is not None:
                if abs(float(row["kgco2e_per_unit"]) - f.kgco2e_per_unit) > 1e-9:
                    raise FactorConflict(f"{f.factor_id} exists with a different value; bump the factor version instead.")
                continue
            conn.execute(
                """insert into emission_factors
                   (id, activity, region, year, unit, scope, kgco2e_per_unit, breakdown, source, source_url, version, notes)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (f.factor_id, f.activity, f.region, f.year, f.unit, f.scope, f.kgco2e_per_unit, json.dumps(f.breakdown),
                 f.source, f.source_url, f.version, f.notes),
            )
            n += 1
    return n


def sync_kb(conn: Conn) -> int:
    """Mirror the knowledge-base chunks (ctrace/knowledge/kb.md) into `kb_chunks` so citations resolve in the database."""
    from ..rag import load_chunks
    chunks = load_chunks()
    with conn.transaction():
        conn.execute("delete from kb_chunks where id like 'kb:%%'")
        with conn.cursor() as cur:
            cur.executemany("insert into kb_chunks (id, title, source, text) values (%s, %s, %s, %s)",
                            [(c.id, c.title, c.source, c.text) for c in chunks])
    return len(chunks)
