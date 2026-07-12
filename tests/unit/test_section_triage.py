"""Unit tests for Section Triage — AI-relevance filtering."""

from src.agents.section_triage import (
    PDFQualityReport,
    TriageResult,
    _build_bill_context_block,
    _extract_orrick_terms,
    _keyword_screen,
    assess_pdf_quality,
    triage_passage,
)


# ---------------------------------------------------------------------------
# PDF Quality Detection
# ---------------------------------------------------------------------------


class TestPDFQuality:
    def test_clean_text_scores_high(self):
        text = (
            "Section 3. Definitions. As used in this chapter, "
            "the term 'artificial intelligence system' means any "
            "machine-based system that can generate outputs such as "
            "predictions, recommendations, or decisions."
        )
        report = assess_pdf_quality(text)
        assert report.score >= 0.9
        assert report.flags == []

    def test_empty_text(self):
        report = assess_pdf_quality("")
        assert report.score == 0.0
        assert "empty_passage" in report.flags

    def test_garbled_chars_penalized(self):
        text = "Section □□□ ■■■ The ▯▯▯ deployer shall ◊◊◊ comply."
        report = assess_pdf_quality(text)
        assert report.score < 0.7
        assert "garbled_chars" in report.flags

    def test_replacement_chars(self):
        text = "The deployer shall \ufffd\ufffd\ufffd disclose \ufffd all uses."
        report = assess_pdf_quality(text)
        assert report.score < 0.8
        assert "encoding_errors" in report.flags

    def test_low_word_density(self):
        # Mostly numbers and symbols, very few real words
        text = "123 456 789 !@# $%^ &*( )_+ === --- ,,, ... ;;;"
        report = assess_pdf_quality(text)
        assert report.score <= 0.7
        assert "low_word_density" in report.flags

    def test_repeated_junk(self):
        text = "Section 1 aaaaaaaaaa bbbbbbbbbb cccccccccc deployer"
        report = assess_pdf_quality(text)
        assert "repeated_junk" in report.flags

    def test_normal_legislative_text(self):
        text = (
            "(a) A developer or deployer of a high-risk artificial "
            "intelligence system shall use reasonable care to protect "
            "consumers from any known or reasonably foreseeable risks "
            "of algorithmic discrimination arising from the intended "
            "and contracted use of the high-risk artificial intelligence "
            "system."
        )
        report = assess_pdf_quality(text)
        assert report.score >= 0.9


# ---------------------------------------------------------------------------
# Orrick Term Extraction
# ---------------------------------------------------------------------------


class TestExtractOrrickTerms:
    def test_extracts_from_ai_scope(self):
        ctx = {"ai_scope": "Automated Decision Systems"}
        terms = _extract_orrick_terms(ctx)
        assert "automated decision systems" in terms

    def test_extracts_from_key_requirements(self):
        ctx = {
            "key_requirements": (
                "Deployers must conduct impact assessments. "
                "Developers must provide transparency reports."
            ),
        }
        terms = _extract_orrick_terms(ctx)
        assert "deployer" in terms or "deployers" in terms
        assert "transparency" in terms

    def test_extracts_from_iapp_topic(self):
        ctx = {"iapp_ai_topic": "Deepfakes; Facial Recognition"}
        terms = _extract_orrick_terms(ctx)
        assert "deepfakes" in terms
        assert "facial recognition" in terms

    def test_empty_context(self):
        terms = _extract_orrick_terms({})
        assert terms == set()

    def test_combined_fields(self):
        ctx = {
            "ai_scope": "AI Governance",
            "key_requirements": "Audit requirements for automated systems.",
            "enforcement_summary": "Civil penalties and injunction.",
        }
        terms = _extract_orrick_terms(ctx)
        assert "ai governance" in terms
        assert "audit" in terms
        assert "penalty" in terms or "penalties" in terms

    def test_ocr_garbled_text_does_not_produce_generic_phrase_terms(self):
        """TA-1 regression: fact_laws.csv's key_requirements is often OCR-scrambled
        (e.g. real row: "the Permanent declaratory relief, Advertising person
        knows is a deceptive..."). The old generic 2+-word phrase regex turned
        noise fragments like this into match terms, auto-marking any passage
        sharing one as `relevant` without ever reaching the LLM. Only curated
        whitelist words should survive, not arbitrary phrase fragments.
        """
        ctx = {
            "key_requirements": (
                "the Permanent declaratory relief, Advertising person knows is "
                "a deceptive and fraudulent deepfake of a candidate for elected "
                "office permanent injunctive relief and in within ninety days"
            ),
        }
        terms = _extract_orrick_terms(ctx)
        # None of these arbitrary noise phrases should appear as match terms —
        # a passage merely containing "permanent declaratory relief" (boilerplate
        # legal language unrelated to AI) must not auto-trigger `relevant`.
        assert "permanent declaratory relief" not in terms
        assert "advertising person knows" not in terms
        assert "deceptive and fraudulent" not in terms
        assert "injunctive relief and" not in terms
        # Curated single-word whitelist terms remain unaffected (no whitelist
        # words happen to appear in this sample, so the set should be empty).
        assert terms == set()


# ---------------------------------------------------------------------------
# Keyword Pre-screen
# ---------------------------------------------------------------------------


class TestKeywordScreen:
    def test_matches_base_ai_terms(self):
        text = "This section applies to artificial intelligence systems."
        matched, keywords = _keyword_screen(text, set())
        assert matched
        assert "artificial intelligence" in keywords

    def test_matches_algorithmic(self):
        text = "The automated decision-making tool shall not discriminate."
        matched, keywords = _keyword_screen(text, set())
        assert matched

    def test_matches_orrick_terms(self):
        orrick_terms = {"impact assessment", "deployer"}
        text = "Each deployer shall complete an impact assessment annually."
        matched, keywords = _keyword_screen(text, orrick_terms)
        assert matched
        assert any("orrick:" in k for k in keywords)

    def test_no_match_on_unrelated(self):
        text = (
            "This section establishes the Department of Revenue "
            "and authorizes the collection of state taxes."
        )
        matched, keywords = _keyword_screen(text, set())
        assert not matched
        assert keywords == []

    def test_matches_regex_patterns(self):
        text = "The AI system deployed by the agency must be tested."
        matched, keywords = _keyword_screen(text, set())
        assert matched

    def test_case_insensitive(self):
        text = "ARTIFICIAL INTELLIGENCE governance framework."
        matched, keywords = _keyword_screen(text, set())
        assert matched


# ---------------------------------------------------------------------------
# Full Triage Flow
# ---------------------------------------------------------------------------


class TestTriagePassage:
    def test_relevant_via_keyword(self):
        text = (
            "A developer of a high-risk artificial intelligence system "
            "shall provide documentation to deployers."
        )
        result = triage_passage(text, {}, llm_provider=None)
        assert result.decision == "relevant"
        assert result.method == "keyword"
        assert len(result.matched_keywords) > 0

    def test_not_relevant_keyword_miss_no_llm(self):
        """Without LLM, keyword-miss passages should be 'uncertain' (conservative)."""
        text = (
            "The Department of Transportation shall maintain records "
            "of all highway construction projects undertaken in the state."
        )
        result = triage_passage(text, {}, llm_provider=None)
        # No AI keywords, no LLM → uncertain (conservative default)
        assert result.decision == "uncertain"
        assert result.method == "passthrough"

    def test_quality_fail_blocks_triage(self):
        # Very low quality text
        text = "\ufffd\ufffd\ufffd □□□ ■■■ ▯▯▯ ◊◊◊ ♦♦♦ ???"
        result = triage_passage(text, {}, llm_provider=None)
        assert result.decision == "not_relevant"
        assert result.method == "quality_fail"
        assert result.pdf_quality_score is not None
        assert result.pdf_quality_score < 0.3

    def test_quality_fail_confidence_is_inverted_from_raw_score(self):
        """TA-4: confidence describes how sure we are about the *decision*
        (not_relevant), not the raw PDF quality score. A near-unreadable
        passage should be reported as HIGH confidence in the not_relevant
        call, not low confidence — those used to be conflated, so a
        0.1-quality passage read (backwards) as "10% confident."
        """
        text = "��� □□□ ■■■ ??? !!!"
        result = triage_passage(text, {}, llm_provider=None)
        assert result.method == "quality_fail"
        assert result.pdf_quality_score is not None
        assert result.pdf_quality_score < 0.3
        # Raw quality score and decision confidence must now differ
        # (inverted) — before this fix they were the exact same number.
        assert result.confidence != result.pdf_quality_score
        assert result.confidence == round(1.0 - result.pdf_quality_score, 3)
        # Worse text quality => higher confidence in the not_relevant skip.
        assert result.confidence > 0.5

    def test_orrick_terms_enhance_keyword_screen(self):
        """Orrick terms should help catch domain-specific language."""
        ctx = {
            "ai_scope": "Automated Employment Decisions",
            "key_requirements": (
                "Employers must conduct bias audits on automated employment "
                "decision tools and provide transparency reports to candidates."
            ),
        }
        text = (
            "Any employer that uses an automated employment decision tool "
            "to screen candidates shall provide notice and conduct bias audits "
            "at least ten business days before use."
        )
        result = triage_passage(text, ctx, llm_provider=None)
        assert result.decision == "relevant"
        assert result.method == "keyword"

    def test_pdf_quality_included_in_result(self):
        text = "Section 5. The deployer of an AI system shall maintain records."
        result = triage_passage(text, {}, llm_provider=None)
        assert result.pdf_quality_score is not None
        assert result.pdf_quality_score > 0.8

    def test_confidence_increases_with_more_keywords(self):
        text_few = "This section covers artificial intelligence."
        text_many = (
            "This artificial intelligence system uses machine learning, "
            "deep learning, and automated decision-making with neural networks "
            "for algorithmic discrimination detection."
        )
        result_few = triage_passage(text_few, {}, llm_provider=None)
        result_many = triage_passage(text_many, {}, llm_provider=None)
        assert result_many.confidence >= result_few.confidence


# ---------------------------------------------------------------------------
# Bill-level context block trimming (TA-3)
# ---------------------------------------------------------------------------


class TestBillContextTrimming:
    def test_definitions_trimmed_to_6000_chars(self):
        ctx = {"bill_definitions": "D" * 30000}
        block = _build_bill_context_block(ctx)
        # Only the trimmed excerpt should appear, not the full 30000 chars.
        assert "D" * 6000 in block
        assert "D" * 6001 not in block

    def test_scope_trimmed_to_5000_chars(self):
        ctx = {"bill_scope": "S" * 20000}
        block = _build_bill_context_block(ctx)
        assert "S" * 5000 in block
        assert "S" * 5001 not in block

    def test_short_fields_pass_through_unmodified(self):
        ctx = {"bill_definitions": "Short definitions block.", "bill_scope": "Short scope."}
        block = _build_bill_context_block(ctx)
        assert "Short definitions block." in block
        assert "Short scope." in block
