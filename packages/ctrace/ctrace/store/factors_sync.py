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
