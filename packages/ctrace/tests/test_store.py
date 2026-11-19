"""ctrace.store: migrations and the factor registry."""
import pytest

from ctrace.factors import all_factors
from ctrace.store.factors_sync import FactorConflict, sync_factors
from ctrace.store.migrate import migrate


def test_migrations_are_idempotent(owner_conn):
    assert migrate(owner_conn) == []


def test_every_code_factor_is_in_the_registry(owner_conn):
    ids = {r["id"] for r in owner_conn.execute("select id from emission_factors")}
    assert {f.factor_id for f in all_factors()} <= ids
    assert sync_factors(owner_conn) == 0


def test_factor_value_cannot_change_without_new_version(owner_conn):
    with owner_conn.transaction(force_rollback=True):
        owner_conn.execute("update emission_factors set kgco2e_per_unit = kgco2e_per_unit + 1 where id = 'ipcc2006-diesel-v1'")
        with pytest.raises(FactorConflict):
            sync_factors(owner_conn)


def test_build_ledger_records_factor_id():
    import pandas as pd

    from ctrace.calculate import build_ledger
    recs = pd.DataFrame([{"period": "2025-03", "activity": "grid_electricity", "quantity": 1000.0, "unit": "kWh",
                          "amount_bdt": 11000, "doc_name": "a.pdf", "doc_sha256": "a" * 64, "bill_no": "1", "supplier": "s"}])
    assert build_ledger(recs).loc[0, "factor_id"] == "ember-bgd-2025-v1"
