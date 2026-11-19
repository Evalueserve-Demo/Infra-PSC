import pytest

from payment_query_platform.intake.classification import Classification


@pytest.mark.parametrize("missing_information", [None, {}])
def test_classification_normalizes_empty_missing_information(missing_information):
    result = Classification.model_validate(
        {
            "is_payment_related": True,
            "request_type": "PAYMENT_STATUS_REQUEST",
            "query_summary": "Payment status requested.",
            "missing_information": missing_information,
            "confidence": 9,
        }
    )

    assert result.missing_information == []