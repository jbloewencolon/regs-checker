"""Unit tests for Orrick similarity validation."""

from src.core.orrick_validation import (
    compute_orrick_similarity,
    validate_extraction_against_orrick,
    _tokenize,
)


class TestTokenize:
    def test_basic_tokenization(self):
        tokens = _tokenize("Automated Decision Systems must comply")
        assert "automated" in tokens
        assert "decision" in tokens
        assert "systems" in tokens
        assert "comply" in tokens

    def test_removes_stop_words(self):
        tokens = _tokenize("the and for that this shall must")
        assert len(tokens) == 0

    def test_removes_short_tokens(self):
        tokens = _tokenize("AI is a big deal")
        assert "ai" not in tokens  # len 2 < 3
        assert "big" in tokens
        assert "deal" in tokens

    def test_empty_input(self):
        assert _tokenize("") == set()
        assert _tokenize(None) == set()


class TestComputeOrrickSimilarity:
    def test_high_overlap(self):
        """Extraction matching Orrick's key requirements should score well."""
        result = compute_orrick_similarity(
            extraction_payload={
                "action": "disclose use of automated decision-making systems",
                "subject": "deployer of high-risk AI systems",
                "condition": "before deployment in Colorado",
            },
            orrick_key_requirements=(
                "Deployers of high-risk AI systems must disclose use of "
                "automated decision-making. Applies to systems deployed in Colorado."
            ),
            orrick_enforcement=None,
        )
        assert result.has_orrick_data is True
        assert result.key_requirements_similarity > 0.15
        assert len(result.matched_tokens) > 0

    def test_no_orrick_data(self):
        """No Orrick metadata should return neutral result."""
        result = compute_orrick_similarity(
            extraction_payload={"action": "comply", "subject": "developer"},
            orrick_key_requirements=None,
            orrick_enforcement=None,
        )
        assert result.has_orrick_data is False
        assert result.combined_score == 0.0
        assert result.matched_tokens == []

    def test_enforcement_similarity(self):
        """Enforcement fields should match against enforcement metadata."""
        result = compute_orrick_similarity(
            extraction_payload={
                "enforcement": {
                    "penalty_type": "civil penalty",
                    "penalty_description": "up to $50,000 per violation",
                    "enforcing_body": "Attorney General",
                },
            },
            orrick_key_requirements=None,
            orrick_enforcement=(
                "Civil penalties up to $50,000 per violation. "
                "Enforced by the Attorney General."
            ),
        )
        assert result.has_orrick_data is True
        assert result.enforcement_similarity > 0.10

    def test_unrelated_content_low_similarity(self):
        """Completely unrelated extraction should have low similarity."""
        result = compute_orrick_similarity(
            extraction_payload={
                "action": "register vehicles annually",
                "subject": "vehicle owners",
            },
            orrick_key_requirements=(
                "Requires transparency in automated hiring decisions. "
                "Deployers must conduct impact assessments."
            ),
            orrick_enforcement="Civil penalties enforced by labor board.",
        )
        assert result.combined_score < 0.10

    def test_evidence_spans_included(self):
        """Evidence span text should be included in similarity computation."""
        result = compute_orrick_similarity(
            extraction_payload={
                "action": "something generic",
                "evidence_spans": [
                    {"text": "deployers of automated decision systems shall disclose"},
                ],
            },
            orrick_key_requirements="Deployers must disclose automated decision systems.",
            orrick_enforcement=None,
        )
        assert result.key_requirements_similarity > 0.10


class TestValidateExtractionAgainstOrrick:
    def test_returns_none_when_no_orrick_context(self):
        result = validate_extraction_against_orrick(
            {"action": "test"},
            {"document_title": "SB205", "jurisdiction": "CO"},
        )
        assert result is None

    def test_returns_result_when_orrick_context_present(self):
        result = validate_extraction_against_orrick(
            {"action": "disclose AI systems", "subject": "deployer"},
            {
                "key_requirements": "Deployers must disclose AI systems.",
                "enforcement_summary": "Civil penalties.",
            },
        )
        assert result is not None
        assert result.has_orrick_data is True
        assert result.combined_score > 0.0
