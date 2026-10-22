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
