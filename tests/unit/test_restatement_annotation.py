"""QA-9c (plan Phase 2b): parse-time scope annotation, and QA-9b (Phase 3,
gated): the pre-extraction excerpt builder that consumes it.

Layers tested here:
  1. annotate_restatement_scope / scope_for_offset — the one-pass tree
     classifier, including parity with assess_extraction_scope on the real
     corpus (SB 926 § 647, AB 2355 § 84504.2).
  2. parser._restatement_scope_meta — parse-time integration on the real
     corpus: SB 926's 8 restatements annotated, AB 2355 carrying the
     added-section set, AR HB1877 and TMP-CA-EMPLOYMENTANDS structurally
     untouched (zero annotations).
  3. Sync consumption (payload_adapter) — stored annotation preferred over
     on-the-fly recomputation, stale engine_version treated as absent,
     flag-off still a no-op. Includes the closure demo for the QA-9a
     added_section_numbers=set() TODO.
  4. build_inscope_excerpt + extractor._prescope_agent_input — the QA-9b
     builder (gated off by default via settings.qa9b_prescope_enabled).
"""

from __future__ import annotations

import json

import pytest

from src.core.config import settings
from src.core.payload_adapter import adapt_payload_for_sync
from src.core.restatement_scope import (
    SCOPE_ENGINE_VERSION,
    annotate_restatement_scope,
    annotation_is_current,
    assess_extraction_scope,
    assess_with_annotation,
    build_inscope_excerpt,
    find_added_section_numbers,
    scope_for_offset,
)
from src.db.models import NormalizedSourceRecord
from src.ingestion.extractor import _prescope_agent_input
from src.ingestion.parser import (
    _group_parallel_versions,
    _restatement_scope_meta,
    _segment_text,
)


def _load_sb926_representative() -> str:
    data = open("output/law_texts/TMP-CA-AMENDMENTOFCAL.txt").read()
    return _segment_text(data)[7][1]


def _load_ab2355() -> tuple[str, set[str]]:
    raw = open("output/law_texts/TMP-CA-AMENDMENTTOTHE.txt").read()
    return _segment_text(raw)[3][1], find_added_section_numbers(raw)


class TestAnnotateRestatementScope:
    @classmethod
    def setup_class(cls):
        cls.sb926_text = _load_sb926_representative()
        cls.sb926_annotation = annotate_restatement_scope(cls.sb926_text, set())
        cls.ab2355_text, cls.ab2355_added = _load_ab2355()

    def test_regions_partition_the_text(self):
        regions = self.sb926_annotation["regions"]
        assert regions[0]["start"] == 0
        for prev, cur in zip(regions, regions[1:]):
            assert prev["end"] == cur["start"]
        assert regions[-1]["end"] == len(self.sb926_text)

    def test_engine_version_stamped(self):
        assert self.sb926_annotation["engine_version"] == SCOPE_ENGINE_VERSION

    def test_json_serializable(self):
        json.dumps(self.sb926_annotation)

    def test_sb926_j4_region_in_scope(self):
        j4 = [r for r in self.sb926_annotation["regions"] if r["label"] == "(j)(4)"]
        assert len(j4) == 1
        assert j4[0]["in_scope"] is True
        assert j4[0]["reason"].startswith("keyword:")

    def test_sb926_loitering_and_window_peeping_out_of_scope(self):
        by_label = {r["label"]: r for r in self.sb926_annotation["regions"]}
        assert by_label["(a)"]["in_scope"] is False
        assert by_label["(i)"]["in_scope"] is False
        assert by_label["(j)(1)"]["in_scope"] is False

    def test_ab2355_formatting_regions_in_scope_via_added_section(self):
        annotation = annotate_restatement_scope(self.ab2355_text, self.ab2355_added)
        by_label = {r["label"]: r for r in annotation["regions"]}
        for label in ("(a)", "(a)(1)", "(a)(2)"):
            assert by_label[label]["in_scope"] is True, label
            assert "84514" in by_label[label]["reason"]
        assert annotation["added_section_numbers"] == sorted(self.ab2355_added)

    def test_ab2355_without_added_sections_reads_out_of_scope(self):
        annotation = annotate_restatement_scope(self.ab2355_text, set())
        by_label = {r["label"]: r for r in annotation["regions"]}
        assert by_label["(a)(1)"]["in_scope"] is False

    def test_no_subdivision_structure_single_region(self):
        text = "This act shall take effect on January 1, 2026, and applies to all contracts."
        annotation = annotate_restatement_scope(text, set())
        assert annotation["regions"] == [
            {"label": None, "start": 0, "end": len(text),
             "in_scope": True, "reason": "no_subdivision_structure"}
        ]

    def test_shared_preamble_region(self):
        text = "647. Except as provided in subdivision (l), every person is guilty. (a) First clause about nothing."
        annotation = annotate_restatement_scope(text, set())
        first = annotation["regions"][0]
        assert first["label"] is None
        assert first["reason"] == "shared_preamble"
        assert first["in_scope"] is True


class TestScopeForOffset:
    def test_offset_resolves_to_region(self):
        text = "(a) Unrelated clause. (b) Creates a computer-generated image of a person."
        annotation = annotate_restatement_scope(text, set())
        # Offset inside (a)
        assert scope_for_offset(annotation, 5)["in_scope"] is False
        # Offset inside (b)
        b_start = text.index("(b)")
        verdict = scope_for_offset(annotation, b_start + 5)
        assert verdict["in_scope"] is True
        assert verdict["subdivision"] == "(b)"

    def test_offset_outside_annotation_safe_default(self):
        annotation = {"engine_version": SCOPE_ENGINE_VERSION,
                      "added_section_numbers": [], "regions": []}
        verdict = scope_for_offset(annotation, 10)
        assert verdict["in_scope"] is True
        assert verdict["reason"] == "offset_out_of_annotation"


class TestAnnotationIsCurrent:
    def test_current_annotation(self):
        assert annotation_is_current(annotate_restatement_scope("(a) x.", set())) is True

    def test_stale_version_rejected(self):
        annotation = annotate_restatement_scope("(a) x.", set())
        annotation["engine_version"] = SCOPE_ENGINE_VERSION - 1
        assert annotation_is_current(annotation) is False

    def test_malformed_rejected(self):
        assert annotation_is_current(None) is False
        assert annotation_is_current("not a dict") is False
        assert annotation_is_current({"engine_version": SCOPE_ENGINE_VERSION}) is False


class TestParityWithAssess:
    """assess_with_annotation over a stored annotation must give the same
    verdict as the on-the-fly assess_extraction_scope — one implementation
    of the rules, no drift between parse-time and sync-time verdicts."""

    @classmethod
    def setup_class(cls):
        cls.sb926_text = _load_sb926_representative()
        cls.ab2355_text, cls.ab2355_added = _load_ab2355()

    @pytest.mark.parametrize("evidence", [
        "A person who intentionally creates and distributes or causes to be "
        "distributed any photo realistic image, digital image, electronic "
        "image, computer image, computer-generated image",
        "A person who looks through a hole or opening, into, or otherwise "
        "views, by means of any instrumentality",
        "An individual who solicits anyone to engage in or who engages in "
        "lewd or dissolute conduct in a public place",
        "text that never appears anywhere in the restatement at all",
    ])
    def test_sb926_verdicts_match(self, evidence):
        annotation = annotate_restatement_scope(self.sb926_text, set())
        assert assess_with_annotation(evidence, self.sb926_text, annotation) == \
            assess_extraction_scope(evidence, self.sb926_text)

    @pytest.mark.parametrize("evidence", [
        "The disclosure area shall have a solid white background and shall "
        "be in a printed or drawn box on the bottom of at least one page",
        "The text shall be in standard Arial Regular type with a type size "
        "of at least 10-point.",
    ])
    def test_ab2355_verdicts_match(self, evidence):
        annotation = annotate_restatement_scope(self.ab2355_text, self.ab2355_added)
        assert assess_with_annotation(evidence, self.ab2355_text, annotation) == \
            assess_extraction_scope(
                evidence, self.ab2355_text,
                added_section_numbers=self.ab2355_added,
            )


class TestParserRestatementScopeMeta:
    """Parse-time integration on the real corpus."""

    def test_sb926_all_eight_restatements_annotated(self):
        data = open("output/law_texts/TMP-CA-AMENDMENTOFCAL.txt").read()
        passages = _segment_text(data)
        pv_meta = _group_parallel_versions(passages)
        scope_meta = _restatement_scope_meta(passages, pv_meta)

        group_indices = {i for i, m in pv_meta.items()
                         if m["parallel_version_group"] == "penal code:647"}
        assert group_indices <= set(scope_meta.keys())
        assert len(group_indices) == 8
        for idx in group_indices:
            assert annotation_is_current(scope_meta[idx])

        # The representative's annotation keeps only (j)(4) among the
        # known-contested regions.
        rep = scope_meta[max(group_indices)]
        by_label = {r["label"]: r for r in rep["regions"]}
        assert by_label["(j)(4)"]["in_scope"] is True
        assert by_label["(a)"]["in_scope"] is False
        assert by_label["(j)(1)"]["in_scope"] is False

    def test_ab2355_annotations_carry_added_section_set(self):
        data = open("output/law_texts/TMP-CA-AMENDMENTTOTHE.txt").read()
        passages = _segment_text(data)
        pv_meta = _group_parallel_versions(passages)
        scope_meta = _restatement_scope_meta(passages, pv_meta)

        group_indices = {i for i, m in pv_meta.items()
                         if m["parallel_version_group"] == "government code:84504.2"}
        assert len(group_indices) == 2
        assert group_indices <= set(scope_meta.keys())
        for idx in group_indices:
            assert "84514" in scope_meta[idx]["added_section_numbers"]

    def test_ar_hb1877_zero_annotations(self):
        # Arkansas's amendment-header shape doesn't match the CA pattern,
        # and no passage forms a parallel-version group.
        data = open("output/law_texts/TMP-AR-OFARKANSASCSAM.txt").read()
        passages = _segment_text(data)
        pv_meta = _group_parallel_versions(passages)
        assert _restatement_scope_meta(passages, pv_meta) == {}

    def test_whole_ai_act_zero_annotations(self):
        # TMP-CA-EMPLOYMENTANDS is wholly an ADS-regulation law with no CA
        # re-enactment headers — the "0% hide on full-AI laws" bar is met
        # structurally: the scope machinery never touches it at all.
        data = open("output/law_texts/TMP-CA-EMPLOYMENTANDS.txt").read()
        passages = _segment_text(data)
        pv_meta = _group_parallel_versions(passages)
        assert _restatement_scope_meta(passages, pv_meta) == {}


class TestSyncPrefersStoredAnnotation:
    """payload_adapter._apply_restatement_scope consumes the parse-time
    annotation when present and current; falls back otherwise. All tests
    here enable the QA-9a gate — flag-off no-op is pinned separately below
    and in test_payload_adapter_qa9a.py."""

    @pytest.fixture(autouse=True)
    def _enable_qa9a_gate(self):
        original = settings.qa9a_scope_filter_enabled
        settings.qa9a_scope_filter_enabled = True
        yield
        settings.qa9a_scope_filter_enabled = original

    # Short and headerless: is_restatement_passage() would NEVER fire on
    # this text, so any hide can only have come through the stored path.
    _PASSAGE = "(a) Every person who loiters in any public place soliciting donations is guilty of disorderly conduct."
    _EVIDENCE = "Every person who loiters in any public place"

    def _payload(self):
        return {
            "subject": "A person",
            "action": "loiters in any public place",
            "evidence_spans": [{"field_name": "action", "text": self._EVIDENCE}],
        }

    def test_stored_annotation_is_authoritative(self):
        annotation = annotate_restatement_scope(self._PASSAGE, set())
        payload = self._payload()
        adapt_payload_for_sync(
            "obligation", payload,
            passage_text=self._PASSAGE,
            passage_metadata={"restatement_scope": annotation},
        )
        assert payload.get("ai_nexus") is False
        assert payload.get("display") is False

    def test_stale_annotation_falls_back_to_trigger(self):
        annotation = annotate_restatement_scope(self._PASSAGE, set())
        annotation["engine_version"] = SCOPE_ENGINE_VERSION - 1
        payload = self._payload()
        adapt_payload_for_sync(
            "obligation", payload,
            passage_text=self._PASSAGE,
            passage_metadata={"restatement_scope": annotation},
        )
        # Fallback path: not a restatement by the on-the-fly trigger → no hide.
        assert payload.get("ai_nexus") is not False

    def test_flag_off_ignores_stored_annotation(self):
        settings.qa9a_scope_filter_enabled = False
        annotation = annotate_restatement_scope(self._PASSAGE, set())
        payload = self._payload()
        adapt_payload_for_sync(
            "obligation", payload,
            passage_text=self._PASSAGE,
            passage_metadata={"restatement_scope": annotation},
        )
        assert payload.get("ai_nexus") is not False

    def test_added_section_todo_closure(self):
        """The exact bug QA-9c closes: sync passes added_section_numbers as
        an empty set (it can't see the whole bill), which on-the-fly would
        wrongly hide an AB 2355-style formatting rule; the stored
        annotation — computed at parse time WITH the added-section set —
        keeps it visible."""
        passage = (
            "Section 84504.2 of the Government Code is amended to read: "
            "84504.2. (a) A print advertisement paid for by a committee "
            "shall include the disclosures required by Section 84514, "
            "displayed as follows: (1) The disclosure area shall have a "
            "solid white background in a printed or drawn box. (2) The "
            "text shall be in standard Arial Regular type."
        )
        evidence = "The disclosure area shall have a solid white background"
        metadata_base = {"parallel_version_group": "government code:84504.2"}

        # Sanity: the on-the-fly path with an empty added-section set
        # (today's sync reality) over-hides this genuine obligation.
        payload = {"subject": "committee", "action": "include disclosures",
                   "evidence_spans": [{"field_name": "action", "text": evidence}]}
        adapt_payload_for_sync(
            "obligation", payload,
            passage_text=passage,
            passage_metadata=dict(metadata_base),
            added_section_numbers=set(),
        )
        assert payload.get("ai_nexus") is False  # the bug being closed

        # With the parse-time annotation (which saw the whole document and
        # its "Section 84514 is added to..." header), the rule stays visible
        # even though sync still passes an empty set.
        annotation = annotate_restatement_scope(passage, {"84514"})
        payload = {"subject": "committee", "action": "include disclosures",
                   "evidence_spans": [{"field_name": "action", "text": evidence}]}
        adapt_payload_for_sync(
            "obligation", payload,
            passage_text=passage,
            passage_metadata={**metadata_base, "restatement_scope": annotation},
            added_section_numbers=set(),
        )
        assert payload.get("ai_nexus") is not False


class TestBuildInscopeExcerpt:
    def test_excerpt_keeps_in_scope_drops_out_of_scope(self):
        text = (
            "(a) Every person who loiters in any public place soliciting "
            "donations is guilty of disorderly conduct. "
            "(b) A person who creates a computer-generated image of another "
            "identifiable person is guilty of disorderly conduct."
        )
        annotation = annotate_restatement_scope(text, set())
        excerpt = build_inscope_excerpt(text, annotation, section_label="Penal Code 647")
        assert excerpt is not None
        assert "computer-generated image" in excerpt
        assert "loiters" not in excerpt
        assert "Penal Code 647" in excerpt
        assert "[...]" in excerpt

    def test_kept_chunks_are_verbatim_slices(self):
        # Evidence-span verification runs against the FULL passage; every
        # non-marker line of the excerpt must therefore be a verbatim
        # substring of the original text.
        text = (
            "(a) Every person who loiters in any public place soliciting "
            "donations is guilty. "
            "(b) A person who creates a computer-generated image is guilty."
        )
        annotation = annotate_restatement_scope(text, set())
        excerpt = build_inscope_excerpt(text, annotation)
        body_lines = [
            line for line in excerpt.split("\n")
            if line != "[...]" and not line.startswith("[Restatement excerpt")
        ]
        assert body_lines
        for line in body_lines:
            assert line in text

    def test_all_in_scope_returns_none(self):
        text = (
            "(a) A deepfake disclosure clause. "
            "(b) A computer-generated image clause."
        )
        annotation = annotate_restatement_scope(text, set())
        assert all(r["in_scope"] for r in annotation["regions"])
        assert build_inscope_excerpt(text, annotation) is None

    def test_nothing_in_scope_returns_none(self):
        # Conservative: never feed agents an empty shell — fall back to
        # the full passage instead.
        annotation = {
            "engine_version": SCOPE_ENGINE_VERSION,
            "added_section_numbers": [],
            "regions": [
                {"label": "(a)", "start": 0, "end": 20,
                 "in_scope": False, "reason": "no_ai_domain_signal"},
            ],
        }
        assert build_inscope_excerpt("(a) Loitering only.", annotation) is None

    def test_stale_annotation_returns_none(self):
        text = "(a) Loitering. (b) A computer-generated image clause."
        annotation = annotate_restatement_scope(text, set())
        annotation["engine_version"] = SCOPE_ENGINE_VERSION - 1
        assert build_inscope_excerpt(text, annotation) is None

    def test_real_sb926_excerpt_massively_smaller(self):
        text = _load_sb926_representative()
        annotation = annotate_restatement_scope(text, set())
        excerpt = build_inscope_excerpt(text, annotation, section_label="Penal Code 647")
        assert excerpt is not None
        # The whole point of Phase 3: ~14K chars of loitering/prostitution
        # boilerplate collapses to the AI-relevant core plus shared preamble.
        assert len(excerpt) < len(text) / 2
        assert "computer-generated image" in excerpt


class TestPrescopeAgentInput:
    """extractor._prescope_agent_input — the QA-9b hook, gated off by default."""

    _TEXT = (
        "(a) Every person who loiters in any public place soliciting "
        "donations is guilty of disorderly conduct. "
        "(b) A person who creates a computer-generated image of another "
        "identifiable person is guilty of disorderly conduct."
    )

    def _record(self, metadata: dict | None = None, text: str | None = None):
        return NormalizedSourceRecord(
            id=1,
            document_version_id=1,
            section_path="Section 647",
            ordinal=0,
            text_content=text if text is not None else self._TEXT,
            text_hash="deadbeef",
            metadata_=metadata or {},
        )

    @pytest.fixture(autouse=True)
    def _restore_flag(self):
        original = settings.qa9b_prescope_enabled
        yield
        settings.qa9b_prescope_enabled = original

    def test_default_flag_is_off(self):
        assert settings.qa9b_prescope_enabled is False

    def test_flag_off_returns_none_even_with_annotation(self):
        annotation = annotate_restatement_scope(self._TEXT, set())
        record = self._record({"restatement_scope": annotation})
        assert _prescope_agent_input(record, self._TEXT) is None

    def test_flag_on_with_annotation_returns_excerpt(self):
        settings.qa9b_prescope_enabled = True
        annotation = annotate_restatement_scope(self._TEXT, set())
        record = self._record({"restatement_scope": annotation})
        excerpt = _prescope_agent_input(record, self._TEXT)
        assert excerpt is not None
        assert "computer-generated image" in excerpt
        assert "loiters" not in excerpt

    def test_flag_on_without_annotation_returns_none(self):
        settings.qa9b_prescope_enabled = True
        record = self._record({})
        assert _prescope_agent_input(record, self._TEXT) is None

    def test_text_mismatch_disables_prescoping(self):
        # Annotation offsets are only valid against text_content as stored;
        # a differing in-flight passage text must fall back to full input,
        # never slice at wrong offsets.
        settings.qa9b_prescope_enabled = True
        annotation = annotate_restatement_scope(self._TEXT, set())
        record = self._record({"restatement_scope": annotation})
        assert _prescope_agent_input(record, self._TEXT + " EXTRA") is None
