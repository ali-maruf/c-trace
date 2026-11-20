def test_database_url_falls_back_to_vercel_postgres_url_and_drops_provider_params(monkeypatch):
    from ctrace.store import db
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_URL", "postgres://u:p@pooler.example.com:6543/postgres?sslmode=require&supa=base-pooler.x")
    assert db.database_url() == "postgres://u:p@pooler.example.com:6543/postgres?sslmode=require"
    monkeypatch.setenv("DATABASE_URL", "postgresql://a:b@h:5432/d")
    assert db.database_url() == "postgresql://a:b@h:5432/d"


def test_supabase_transaction_pooler_is_switched_to_session_mode(monkeypatch):
    from ctrace.store import db
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_URL", "postgres://u.ref:p@aws-1-ap-south-1.pooler.supabase.com:6543/postgres?sslmode=require&supa=base-pooler.x")
    assert db.database_url() == "postgres://u.ref:p@aws-1-ap-south-1.pooler.supabase.com:5432/postgres?sslmode=require"
    monkeypatch.setenv("CTRACE_DB_SESSION_POOLER", "0")
    assert ":6543/" in db.database_url()
