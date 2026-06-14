"""Unit tests for ModelConfigStore provider persistence (dashboard toggle).

The ``provider`` field is the runtime source of truth for the local-vs-NVIDIA
extraction toggle.  These tests verify it round-trips through save/load and
defaults sensibly for backward-compatible config files.
"""
from __future__ import annotations

import json

import pytest

import src.core.model_config as mc
from src.core.model_config import AgentModelConfig, ModelConfigStore


@pytest.fixture
def temp_config(tmp_path, monkeypatch):
    """Point CONFIG_PATH at a temp file for isolated save/load."""
    cfg_file = tmp_path / "agent_models.json"
    monkeypatch.setattr(mc, "CONFIG_PATH", cfg_file)
    return cfg_file


class TestProviderPersistence:
    def test_default_provider_is_local(self, temp_config, monkeypatch):
        monkeypatch.setattr(mc.settings, "extraction_provider", "local")
        store = ModelConfigStore.defaults()
        assert store.provider == "local"

    def test_defaults_seed_provider_from_settings(self, temp_config, monkeypatch):
        monkeypatch.setattr(mc.settings, "extraction_provider", "nvidia")
        store = ModelConfigStore.defaults()
        assert store.provider == "nvidia"

    def test_provider_round_trips_through_save_load(self, temp_config):
        store = ModelConfigStore(
            agents={"obligation": AgentModelConfig(model="m", max_tokens=1024)},
            provider="nvidia",
        )
        store.save()
        reloaded = ModelConfigStore.load()
        assert reloaded.provider == "nvidia"
        assert reloaded.agents["obligation"].model == "m"

    def test_saved_json_has_provider_key(self, temp_config):
        ModelConfigStore(agents={}, provider="nvidia").save()
        data = json.loads(temp_config.read_text())
        assert data["provider"] == "nvidia"
        assert "agents" in data

    def test_legacy_config_without_provider_key_seeds_from_settings(self, temp_config, monkeypatch):
        # Simulate a pre-toggle agent_models.json with no "provider" key.
        temp_config.write_text(json.dumps({
            "agents": {"obligation": {"model": "x", "max_tokens": 2048}}
        }))
        monkeypatch.setattr(mc.settings, "extraction_provider", "local")
        store = ModelConfigStore.load()
        assert store.provider == "local"
        assert store.agents["obligation"].model == "x"

    def test_switching_provider_persists(self, temp_config):
        # Save local, then flip to nvidia — mirrors the dashboard toggle flow.
        ModelConfigStore(agents={}, provider="local").save()
        store = ModelConfigStore.load()
        store.provider = "nvidia"
        store.save()
        assert ModelConfigStore.load().provider == "nvidia"
