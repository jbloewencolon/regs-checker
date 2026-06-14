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

    def test_saved_json_has_provider_and_providers_keys(self, temp_config):
        ModelConfigStore(agents={}, provider="nvidia").save()
        data = json.loads(temp_config.read_text())
        assert data["provider"] == "nvidia"
        assert "providers" in data

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


class TestProviderSeparation:
    """Each backend keeps its own per-agent settings — editing one must not
    clobber the other (the core of the LM Studio vs NVIDIA separation)."""

    def test_providers_are_stored_independently(self, temp_config):
        store = ModelConfigStore(provider="local", providers={
            "local": {"obligation": AgentModelConfig(model="gemma")},
            "nvidia": {"obligation": AgentModelConfig(model="gpt-oss-120b")},
        })
        store.save()
        reloaded = ModelConfigStore.load()
        assert reloaded.agents_for("local")["obligation"].model == "gemma"
        assert reloaded.agents_for("nvidia")["obligation"].model == "gpt-oss-120b"

    def test_active_provider_drives_agents_view(self, temp_config):
        store = ModelConfigStore(provider="nvidia", providers={
            "local": {"obligation": AgentModelConfig(model="gemma")},
            "nvidia": {"obligation": AgentModelConfig(model="gpt-oss-120b")},
        })
        assert store.agents["obligation"].model == "gpt-oss-120b"
        assert store.get("obligation").model == "gpt-oss-120b"
        store.provider = "local"
        assert store.get("obligation").model == "gemma"

    def test_set_agents_preserves_other_provider(self, temp_config):
        store = ModelConfigStore.defaults()
        store.set_agents("nvidia", {"triage": AgentModelConfig(model="meta/llama-3.1-8b-instruct")})
        # The local block must remain intact after editing nvidia.
        assert "obligation" in store.agents_for("local")
        assert store.agents_for("nvidia")["triage"].model == "meta/llama-3.1-8b-instruct"

    def test_legacy_flat_config_migrates_under_active_provider(self, temp_config, monkeypatch):
        # Old single-map format with provider=nvidia → those agents become the
        # nvidia block; local is synthesised from defaults.
        temp_config.write_text(json.dumps({
            "provider": "nvidia",
            "agents": {"obligation": {"model": "openai/gpt-oss-120b", "max_tokens": 4096}},
        }))
        store = ModelConfigStore.load()
        assert store.provider == "nvidia"
        assert store.agents_for("nvidia")["obligation"].model == "openai/gpt-oss-120b"
        # local block exists (filled from defaults) and is independent.
        assert "obligation" in store.agents_for("local")
