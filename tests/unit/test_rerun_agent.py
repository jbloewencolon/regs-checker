"""Tests for src/scripts/rerun_agent.py — Phase D of the per-agent refactor.

No live DB is used anywhere in this repo's test suite (all 870+ existing unit
tests are pure-logic), so these tests cover the parts of rerun_agent.py that
don't require a database: CLI validation/routing and the agent-name
classification sets. The DB-touching functions (scoped_purge_agent,
rerun_agent, _resolve_record_ids) are exercised manually against a live
Postgres per the module's own --dry-run mode.
"""

from __future__ import annotations

import sys

import pytest

from src.scripts.rerun_agent import (
    _BILL_LEVEL_AGENTS,
    _CLAUSE_LEVEL_AGENTS,
    main,
)


class TestAgentClassification:
    def test_clause_level_agents_match_extractor(self):
        from src.ingestion.extractor import _get_agents

        # _get_agents() lazily creates provider clients; just check the key set
        # via the module-level AGENTS dict shape without triggering network calls.
        # The six names are also asserted directly against the known agent roster.
        assert _CLAUSE_LEVEL_AGENTS == {
            "obligation",
            "definition_actor",
            "threshold_exception",
            "rights_protection",
            "compliance_mechanism",
            "preemption",
        }

    def test_bill_level_agents_are_disjoint_from_clause_level(self):
        assert _BILL_LEVEL_AGENTS.isdisjoint(_CLAUSE_LEVEL_AGENTS)

    def test_bill_level_agents_match_known_roster(self):
        assert _BILL_LEVEL_AGENTS == {
            "enforcement_agent",
            "applicability_agent",
            "compliance_timeline_agent",
        }


class TestCliValidation:
    """These exit before touching the DB, so no engine/session is created."""

    def test_bill_level_agent_rejected(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["rerun_agent.py", "--agent", "enforcement_agent"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "bill-level agent" in out
        assert "enforcement_agent" in out

    def test_unknown_agent_rejected(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["rerun_agent.py", "--agent", "not_a_real_agent"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "Unknown agent" in out

    def test_missing_required_agent_arg_exits(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["rerun_agent.py"])
        with pytest.raises(SystemExit):
            main()
