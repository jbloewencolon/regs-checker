"""Agent ↔ model configuration with provider-separated settings.

Stores per-agent model assignments + inference settings in
``config/agent_models.json``.  Settings are kept **separate per backend**:
each provider ("local" LM Studio and "nvidia" hosted API) has its own full set
of per-agent configs, so switching backends never reuses the other backend's
model names (which would 404 — e.g. sending ``google/gemma-...`` to NVIDIA).

Model discovery is also provider-specific:
  - ``fetch_available_models()`` queries LM Studio ``/v1/models`` (loaded models)
  - ``fetch_nvidia_models()`` queries the NVIDIA catalog endpoint
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.core.config import settings

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "agent_models.json"

# Backends that have their own separate settings block.
PROVIDERS: tuple[str, ...] = ("local", "nvidia")

# Agents that appear in the config UI.  Keys must match the agent_name
# attribute on each agent (or "triage" for the section triage agent).
# Order here is the display order on the Models page.
AGENT_DISPLAY: dict[str, dict[str, str]] = {
    "obligation": {
        "label": "Obligation",
        "description": "Extracts legal obligations, mandates, and prohibitions",
    },
    "rights_protection": {
        "label": "Rights & Protection",
        "description": "Extracts individual rights, opt-outs, and consent requirements",
    },
    "definition_actor": {
        "label": "Definition / Actor",
        "description": "Extracts defined terms and regulated actors",
    },
    "threshold_exception": {
        "label": "Threshold / Exception",
        "description": "Extracts thresholds, exemptions, and carve-outs",
    },
    "compliance_mechanism": {
        "label": "Compliance Mechanism",
        "description": "Extracts enforcement, penalties, and audit requirements",
    },
    "preemption": {
        "label": "Preemption",
        "description": "Extracts federal/state/local preemption signals",
    },
    "triage": {
        "label": "Section Triage",
        "description": "Classifies passages as AI-relevant before extraction",
    },
    "enforcement_agent": {
        "label": "Enforcement (bill)",
        "description": "Bill-level synthesis of enforcement authority and penalties",
    },
    "applicability_agent": {
        "label": "Applicability (bill)",
        "description": "Bill-level synthesis of scope and who/what is covered",
    },
    "compliance_timeline_agent": {
        "label": "Compliance Timeline (bill)",
        "description": "Bill-level synthesis of effective dates and deadlines",
    },
}

# Per-agent output-token budgets.  Pre-emptively generous so reasoning models
# (gpt-oss-120b emits a chain-of-thought trace before the JSON) have headroom
# and don't truncate with finish_reason=length.  Local models clamp this to
# local_extraction_max_tokens in base.py, so the extra is harmless there.
_AGENT_MAX_TOKENS: dict[str, int] = {
    "obligation":                6144,
    "rights_protection":         4096,
    "definition_actor":          2048,
    "threshold_exception":       4096,
    "compliance_mechanism":      4096,
    "preemption":                1536,
    "triage":                     512,
    "enforcement_agent":         3072,
    "applicability_agent":       4096,
    "compliance_timeline_agent": 4096,
}


@dataclass
class AgentModelConfig:
    """Per-agent model + inference settings."""
    model: str = ""
    max_tokens: int = 65536
    context_length: int = 131072
    temperature: float = 0.0
    reasoning_effort: str | None = None  # "low", "medium", "high", "off", or None
    top_p: float | None = None           # nucleus sampling (0–1); None = provider default

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_agents(raw_agents: dict[str, Any]) -> dict[str, AgentModelConfig]:
    """Parse a JSON agents map into AgentModelConfig objects, dropping unknown keys."""
    valid = {f.name for f in AgentModelConfig.__dataclass_fields__.values()}
    return {
        name: AgentModelConfig(**{k: v for k, v in cfg.items() if k in valid})
        for name, cfg in raw_agents.items()
    }


class ModelConfigStore:
    """Provider-separated agent configuration.

    ``providers`` maps backend name → {agent_name: AgentModelConfig}.  The
    active backend is ``provider``; ``.agents`` and ``.get()`` resolve against
    it, so existing callers (``cfg.agents``, ``cfg.get(name)``) keep working
    while each backend keeps its own settings.

    ``provider`` is the runtime source of truth for the dashboard backend
    toggle, seeded from ``settings.extraction_provider`` on first load.
    """

    def __init__(
        self,
        agents: dict[str, AgentModelConfig] | None = None,
        provider: str = "local",
        providers: dict[str, dict[str, AgentModelConfig]] | None = None,
    ) -> None:
        self.provider = provider or "local"
        if providers is not None:
            self.providers = providers
        else:
            # Back-compat: a single ``agents`` map seeds the active provider.
            self.providers = {}
            if agents is not None:
                self.providers[self.provider] = agents

    # ------------------------------------------------------------------
    # Active-provider views (back-compat surface)
    # ------------------------------------------------------------------

    @property
    def agents(self) -> dict[str, AgentModelConfig]:
        """Read-only view of the active provider's agent map."""
        return self.providers.get(self.provider, {})

    def agents_for(self, provider: str) -> dict[str, AgentModelConfig]:
        """Return the agent map for a specific provider (empty if unset)."""
        return self.providers.get(provider, {})

    def set_agents(self, provider: str, agents: dict[str, AgentModelConfig]) -> None:
        """Replace one provider's agent map, leaving other providers untouched."""
        self.providers[provider] = agents

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @classmethod
    def load(cls) -> ModelConfigStore:
        """Load from JSON, migrating the legacy flat format if needed."""
        if CONFIG_PATH.exists():
            try:
                raw = json.loads(CONFIG_PATH.read_text())
                provider = raw.get("provider") or settings.extraction_provider or "local"

                if "providers" in raw:
                    # Current format: one agents map per backend.
                    providers = {
                        prov: _parse_agents(block.get("agents", {}))
                        for prov, block in raw["providers"].items()
                    }
                elif "agents" in raw:
                    # Legacy flat format: those agents belong to the active
                    # backend; synthesise the other backend(s) from defaults.
                    providers = {provider: _parse_agents(raw["agents"])}
                else:
                    return cls.defaults()

                # Ensure every known backend has a settings block.
                for prov in PROVIDERS:
                    providers.setdefault(prov, cls._default_agents(prov))

                return cls(providers=providers, provider=provider)
            except Exception:
                logger.warning("Corrupt agent_models.json — using defaults", exc_info=True)
        return cls.defaults()

    def save(self) -> None:
        """Persist current config to JSON (all providers)."""
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "provider": self.provider,
            "providers": {
                prov: {"agents": {name: cfg.to_dict() for name, cfg in agents.items()}}
                for prov, agents in self.providers.items()
            },
        }
        CONFIG_PATH.write_text(json.dumps(data, indent=2) + "\n")
        logger.info("Saved agent model config to %s (provider=%s)", CONFIG_PATH, self.provider)

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------

    @staticmethod
    def _default_agents(provider: str) -> dict[str, AgentModelConfig]:
        """Build a default agent map for one backend.

        Large input context, small output: models ingest full bill passages
        but only produce a single JSON object per extraction.  NVIDIA defaults
        to a single model uniformly (gpt-oss-120b); operators tier it per-agent
        from the Models page.
        """
        agents: dict[str, AgentModelConfig] = {}
        for name in AGENT_DISPLAY:
            max_tok = _AGENT_MAX_TOKENS.get(name, 2048)
            temp = 0.0 if name == "triage" else settings.extraction_temperature
            if provider == "nvidia":
                model = settings.nvidia_extraction_model
                agents[name] = AgentModelConfig(
                    model=model,
                    max_tokens=max_tok,
                    context_length=settings.local_context_length,
                    temperature=temp,
                    reasoning_effort=None,
                )
            else:
                model = (
                    settings.local_triage_model
                    if name == "triage"
                    else settings.local_extraction_model
                )
                agents[name] = AgentModelConfig(
                    model=model,
                    max_tokens=max_tok,
                    context_length=settings.local_context_length,
                    temperature=temp,
                    reasoning_effort="off",
                )
        return agents

    @classmethod
    def defaults(cls) -> ModelConfigStore:
        """Built-in defaults for every backend, tuned for legislative extraction."""
        providers = {prov: cls._default_agents(prov) for prov in PROVIDERS}
        provider = settings.extraction_provider or "local"
        return cls(providers=providers, provider=provider)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get(self, agent_name: str) -> AgentModelConfig:
        """Return config for an agent under the active provider, with fallback."""
        agents = self.agents
        if agent_name in agents:
            return agents[agent_name]
        fallback_model = (
            settings.nvidia_extraction_model
            if self.provider == "nvidia"
            else settings.local_extraction_model
        )
        return AgentModelConfig(
            model=fallback_model,
            max_tokens=settings.local_extraction_max_tokens,
            context_length=settings.local_context_length,
            temperature=settings.extraction_temperature,
        )


# ------------------------------------------------------------------
# Model discovery
# ------------------------------------------------------------------

def fetch_available_models(timeout: float = 3.0) -> list[dict[str, Any]]:
    """Query LM Studio /v1/models and return a list of model dicts.

    Each dict has at least ``id`` (the model name string).  Reflects the models
    actually **loaded** into LM Studio.  Returns an empty list on connection
    failure.
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


def fetch_nvidia_models(timeout: float = 8.0) -> list[str]:
    """Query the NVIDIA catalog endpoint and return available model id strings.

    Returns an empty list if the key is unset or the endpoint is unreachable.
    """
    if not settings.nvidia_api_key:
        return []
    import httpx
    url = f"{settings.nvidia_base_url.rstrip('/')}/models"
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {settings.nvidia_api_key}"},
            timeout=timeout,
        )
        resp.raise_for_status()
        return sorted(m["id"] for m in resp.json().get("data", []))
    except Exception:
        logger.debug("Could not reach NVIDIA catalog at %s", url, exc_info=True)
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
