import pytest

from app.counterparty_service import extract_counterparty


@pytest.mark.parametrize(
    "narration",
    [
        "UPI IN/123456789012/synth1234-1@oksbi/SBI/4321",
        "UPI IN/123456789012/synth1234- 1@oksbi/SBI/4321",  # a PDF line wrap inside the handle
        "UPIOUT/123456789012/synth1234-  1@oksbi/SBI/4321",
    ],
)
def test_a_handle_split_by_a_pdf_line_wrap_is_the_same_counterparty(narration):
    candidate = extract_counterparty(narration, "uncategorized")

    assert candidate is not None
    assert candidate.alias_value.lower() == "synth1234-1@oksbi"


def test_ordinary_text_with_a_hyphen_is_not_joined_into_a_handle():
    assert extract_counterparty("MONTHLY FEE- 12 MARCH", "uncategorized") is None


@pytest.mark.parametrize(
    ("narration", "name", "alias"),
    [
        ("UPI/DR/123456789012/ASHA nidnam2813-4@fbl", "Nidnam", "nidnam2813-4@fbl"),
        ("UPI/DR/123456789012/Abdul Ra/SBIN/rasheedkannur474@okhdfc/", "Rasheedkannur", None),
        ("UPI/DR/123456789012/BRIGHT q123456789@ybl", "Bright", "q123456789@ybl"),
        ("UPI/DR/123456789012/KWALITY/UTIB/mab.0371234@axis/U", "Kwality", None),
        ("UPI/DR/123456789012/q123456789@ybl/UPI", "q123456789@ybl", "q123456789@ybl"),
        ("UPIOUT/123456789012/mab.037@axis", "mab.037@axis", None),
    ],
)
def test_handles_get_a_readable_name_without_changing_the_grouping_key(narration, name, alias):
    candidate = extract_counterparty(narration, "uncategorized")

    assert candidate is not None
    assert candidate.display_name == name
    if alias:
        assert candidate.alias_value == alias
