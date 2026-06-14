"""Global test fixtures and hermetic configuration.

Tests must not depend on the operator's committed ``config/agent_models.json``.
That file may select the NVIDIA backend, which raises at agent construction
when ``NVIDIA_API_KEY`` is unset (e.g. in CI).  Some test modules build real
agents at import time, so we pin a deterministic, key-free config (local LM
Studio) *before* collection by setting the module singleton and redirecting
``CONFIG_PATH`` at import of this conftest.
"""
from __future__ import annotations

import pathlib
import tempfile

import src.core.model_config as mc
from src.core.model_config import ModelConfigStore

# Redirect persistence to a throwaway temp file so any save/reload during a
# test run stays isolated from the repo's real config.
_TEST_CONFIG_PATH = pathlib.Path(tempfile.gettempdir()) / "regs_checker_test_agent_models.json"
mc.CONFIG_PATH = _TEST_CONFIG_PATH

# Seed a deterministic, local-backend config as the active singleton.
_test_store = ModelConfigStore.defaults()
_test_store.provider = "local"
mc._store = _test_store
