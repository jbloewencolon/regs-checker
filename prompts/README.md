# Prompt Templates

Versioned prompt templates tracked in git (per project non-negotiables).

Each extraction agent's system prompt and extraction prompt are defined in
their respective module under `src/agents/`. This directory is reserved for
future prompt template files if prompt engineering iterations require
externalized templates.

## Current Agents

| Agent | Module | Absorbs |
|-------|--------|---------|
| Obligation | `src/agents/obligation.py` | obligation + timeline + enforcement |
| Definition & Actor | `src/agents/definition_actor.py` | definition + actor_mapping + framework_ref |
| Threshold & Exception | `src/agents/threshold_exception.py` | threshold + exception |
| Ambiguity | `src/agents/ambiguity.py` | ambiguity (unchanged) |
