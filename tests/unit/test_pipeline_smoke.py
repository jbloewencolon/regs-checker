"""SFH-1l (audit B10/SF-11b) — one-passage pipeline smoke test.

Green unit tests don't prove the pipeline wires together: tasks.md once
carried three NameError crash fixes sitting unmerged while CI was green,
because no test drove a passage through the actual seams. This test walks
one passage through the real modules — triage keyword layer, signal
routing, an extraction agent with a stubbed provider, confidence scoring,
and the sync payload adapter — so import-time and wiring errors fail CI.

Deliberately NOT covered here: DB persistence (no Postgres in CI unit
stage) and live LLM calls (stubbed). This is a wiring check, not an
accuracy check — accuracy is EA1's job.
"""

from __future__ import annotations

from unittest.mock import patch

from src.core.llm_provider import LLMUsage

_PASSAGE = (
    "A developer of a high-risk artificial intelligence system shall complete "
    "an impact assessment before deployment and shall provide disclosure to "
    "consumers. The attorney general may impose a civil penalty of up to "
    "$20,000 per violation."
)

_AGENT_RESPONSE = (
    '{"extractions": [{'
    '"subject": "developer", "subject_normalized": "developer", '
    '"modality": "shall", '
    '"action": "complete an impact assessment before deployment", '
    '"evidence_spans": [{"text": "shall complete an impact assessment before '
    'deployment", "field_name": "action"}]'
    "}]}"
)


def test_one_passage_end_to_end_wiring():
    # --- 1. Triage keyword layer (no LLM needed for a keyword hit) ---
    from src.agents.section_triage import _BASE_AI_KEYWORDS  # import = wiring check

    assert any(k in _PASSAGE.lower() for k in _BASE_AI_KEYWORDS), \
        "smoke passage must trip the triage keyword layer"

    # --- 2. Signal routing selects the obligation agent for this passage ---
    from src.ingestion.routing import select_agent_names_with_decision

    decision = select_agent_names_with_decision(
        _PASSAGE,
        {"obligation", "definition_actor", "threshold_exception",
         "rights_protection", "compliance_mechanism", "preemption"},
        recall_sample_rate=0.0,
    )
    assert "obligation" in decision.selected

    # --- 3. Extraction agent end-to-end with a stubbed provider ---
    with patch("src.agents.base.get_extraction_provider"), \
         patch("src.core.model_config.get_config") as mock_cfg:
        mock_cfg.return_value.agents = {}
        from src.agents.obligation import ObligationAgent
        agent = ObligationAgent()

    usage = LLMUsage(input_tokens=200, output_tokens=80)
    with patch.object(
        agent, "_call_llm",
        return_value=(_AGENT_RESPONSE, usage, "stub-model", "stop"),
    ):
        result = agent.extract(_PASSAGE, {"jurisdiction": "CO"})

    assert result.abstention is None
    assert len(result.extractions) == 1
    item = result.extractions[0]
    assert item["subject"] == "developer"
    # Evidence span verification ran against the real passage text.
    assert item["evidence_spans"][0]["verified"] is True
    assert result.truncated is False
    assert result.was_repaired is False

    # --- 4. Confidence scoring on the real payload ---
    from src.core.confidence import compute_confidence
    from src.schemas.extraction import ObligationPayload

    confidence = compute_confidence(
        schema_valid=True,
        evidence_spans=item["evidence_spans"],
        extraction_payload=item,
        schema_class=ObligationPayload,
        passage_text=_PASSAGE,
    )
    assert confidence.tier in ("A", "B", "C", "D")
    assert 0.0 <= confidence.total_score <= 1.0

    # --- 5. Sync payload adapter (the PN-facing shape) ---
    from src.core.payload_adapter import adapt_payload_for_sync

    adapted = adapt_payload_for_sync("obligation", item)
    assert adapted["subject"] == "developer"
    assert adapted["actor_role_rc"] == "developer"
    assert adapted["obligation_family"] == "impact_assessment"
