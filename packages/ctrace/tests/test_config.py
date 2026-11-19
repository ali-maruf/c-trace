"""Workspace path resolution after the monorepo move."""
import importlib

from ctrace import config


def test_root_points_at_workspace_with_demo_data():
    assert (config.ROOT / "data" / "samples" / "factory_profile.json").is_file()
    assert (config.BILLS_DIR).is_dir() and any(config.BILLS_DIR.glob("*.pdf"))
    assert (config.KNOWLEDGE_DIR / "kb.md").is_file()


def test_ctrace_root_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("CTRACE_ROOT", str(tmp_path))
    try:
        reloaded = importlib.reload(config)
        assert tmp_path.resolve() == reloaded.ROOT
        assert tmp_path.resolve() / "models" == reloaded.MODELS_DIR
    finally:
        monkeypatch.delenv("CTRACE_ROOT")
        importlib.reload(config)
