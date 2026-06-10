"""Agent ↔ model configuration with LM Studio discovery.

Stores per-agent model assignments + settings in ``config/agent_models.json``.
Queries LM Studio ``/v1/models`` endpoint for available models at runtime.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.core.config import settings

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "agent_models.json"

# Agents that appear in the config UI.  Keys must match the agent_name
# attribute on each BaseExtractionAgent subclass (or "triage" for the
# section triage agent which is not a BaseExtractionAgent).
AGENT_DISPLAY: dict[str, dict[str, str]] = {
    "obligation":           {"label": "Obligation",           "description": "Extracts legal obligations, mandates, and prohibitions"},
    "rights_protection":    {"label": "Rights & Protection",  "description": "Extracts individual rights, opt-outs, and consent requirements"},
    "definition_actor":     {"label": "Definition / Actor",   "description": "Extracts defined terms and regulated actors"},
    "threshold_exception":  {"label": "Threshold / Exception","description": "Extracts thresholds, exemptions, and carve-outs"},
    "compliance_mechanism": {"label": "Compliance Mechanism", "description": "Extracts enforcement, penalties, and audit requirements"},
    "preemption":           {"label": "Preemption",           "description": "Extracts federal/state/local preemption signals"},
    "triage":               {"label": "Section Triage",       "description": "Classifies passages as AI-relevant before extraction"},
}


@dataclass
class AgentModelConfig:
    """Per-agent model + inference settings."""
    model: str = ""
    max_tokens: int = 65536
    context_length: int = 131072
    temperature: float = 0.0
    reasoning_effort: str | None = None  # "low", "medium", "high", or None (model default)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ModelConfigStore:
    """Full config mapping agent names → settings."""
    agents: dict[str, AgentModelConfig] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @classmethod
    def load(cls) -> ModelConfigStore:
        """Load from JSON file, falling back to built-in defaults."""
        if CONFIG_PATH.exists():
            try:
                raw = json.loads(CONFIG_PATH.read_text())
                valid_fields = {f.name for f in AgentModelConfig.__dataclass_fields__.values()}
                agents = {
                    name: AgentModelConfig(**{k: v for k, v in cfg.items() if k in valid_fields})
                    for name, cfg in raw.get("agents", {}).items()
                }
                return cls(agents=agents)
            except Exception:
                logger.warning("Corrupt agent_models.json — using defaults", exc_info=True)
        return cls.defaults()

    def save(self) -> None:
        """Persist current config to JSON."""
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {"agents": {name: cfg.to_dict() for name, cfg in self.agents.items()}}
        CONFIG_PATH.write_text(json.dumps(data, indent=2) + "\n")
        logger.info("Saved agent model config to %s", CONFIG_PATH)

    @classmethod
    def defaults(cls) -> ModelConfigStore:
        """Built-in defaults — tuned for legislative extraction workload.

        Large input context, small output: models ingest full bill passages
        but only produce a single JSON object per extraction.
        """
        # Per-agent max_tokens — tuned for structured JSON output.
        # All agents use reasoning_effort="off" because:
        #   - Gemma 4 only supports "on"/"off" (not "low"/"medium"); passing
        #     unsupported values causes LM Studio to fall back to "on"
        #   - With "on", Gemma produces reasoning_tokens=1 (empty) anyway,
        #     so "off" just eliminates the overhead without quality loss
        # The adaptive retry in base.py still escalates on truncation.
        _AGENT_SETTINGS: dict[str, int] = {
            "obligation":           2048,
            "rights_protection":    1024,
            "definition_actor":      768,
            "threshold_exception":  2048,
            "compliance_mechanism": 1024,
            "preemption":            768,
            "triage":                256,
        }
        agents = {}
        for name in AGENT_DISPLAY:
            max_tok = _AGENT_SETTINGS.get(name, 1024)
            if name == "triage":
                agents[name] = AgentModelConfig(
                    model=settings.local_triage_model,
                    max_tokens=max_tok,
                    context_length=settings.local_context_length,
                    temperature=0.0,
                    reasoning_effort="off",
                )
            else:
                agents[name] = AgentModelConfig(
                    model=settings.local_extraction_model,
                    max_tokens=max_tok,
                    context_length=settings.local_context_length,
                    temperature=settings.extraction_temperature,
                    reasoning_effort="off",
                )
        return cls(agents=agents)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get(self, agent_name: str) -> AgentModelConfig:
        """Return config for an agent, falling back to extraction defaults."""
        if agent_name in self.agents:
            return self.agents[agent_name]
        return AgentModelConfig(
            model=settings.local_extraction_model,
            max_tokens=settings.local_extraction_max_tokens,
            context_length=settings.local_context_length,
            temperature=settings.extraction_temperature,
        )


# ------------------------------------------------------------------
# LM Studio model discovery
# ------------------------------------------------------------------

def fetch_available_models(timeout: float = 3.0) -> list[dict[str, Any]]:
    """Query LM Studio /v1/models and return a list of model dicts.

    Each dict has at least ``id`` (the model name string).
    Returns an empty list on connection failure.
    """
    import httpx  # lazy — not installed in all environments (e.g., test)
    url = f"{settings.local_llm_url}/v1/models"
    try:
        resp = httpx.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])
    except Exception:
        logger.debug("Could not reach LM Studio at %s", url, exc_info=True)
        return []


# Module-level singleton — loaded once, updated via save/reload.
_store: ModelConfigStore | None = None


def get_config() -> ModelConfigStore:
    """Return the current config (loads from disk on first call)."""
    global _store
    if _store is None:
        _store = ModelConfigStore.load()
    return _store


def reload_config() -> ModelConfigStore:
    """Force-reload config from disk."""
    global _store
    _store = ModelConfigStore.load()
    return _store


def save_config(store: ModelConfigStore) -> None:
    """Save and update the module-level singleton."""
    global _store
    store.save()
    _store = store
