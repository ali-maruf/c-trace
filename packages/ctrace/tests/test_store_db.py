def test_database_url_falls_back_to_vercel_postgres_url_and_drops_provider_params(monkeypatch):
    from ctrace.store import db
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_URL", "postgres://u:p@pooler.example.com:6543/postgres?sslmode=require&supa=base-pooler.x")
    assert db.database_url() == "postgres://u:p@pooler.example.com:6543/postgres?sslmode=require"
    monkeypatch.setenv("DATABASE_URL", "postgresql://a:b@h:5432/d")
    assert db.database_url() == "postgresql://a:b@h:5432/d"
