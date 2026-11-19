"""Tests for which classified mails the intake Lambda hands to a person."""

import uuid

import pytest

from payment_query_platform.intake import guardrail, invoke_agents
from payment_query_platform.mailbox.categories import NEEDS_ATTENTION


@pytest.fixture
def classify(monkeypatch):
    """Classify one mail with the runtime faked; returns what was escalated and labelled."""
    escalated, labelled, returned = [], [], []
    monkeypatch.setattr(invoke_agents, "screen", lambda subject, body: guardrail.GuardrailResult(blocked=False))
    monkeypatch.setattr(invoke_agents, "_mark_in_outlook", lambda message_id, category: labelled.append(category) or category)
    monkeypatch.setattr(invoke_agents, "escalate", lambda *args: escalated.append(args))
    monkeypatch.setattr(invoke_agents.metrics, "put", lambda *a, **k: None)
    monkeypatch.setattr(invoke_agents, "_get_database", lambda: None)
    monkeypatch.setattr(invoke_agents, "_record_classification", lambda *a, **k: True)
    monkeypatch.setattr(invoke_agents, "_close_if_not_ours", lambda *a: None)

    def run(request_type, confidence=10, payment=True, account_change=False):
        classification = {"is_payment_related": payment, "request_type": request_type, "confidence": confidence,
                          "mentions_account_change": account_change}
        monkeypatch.setattr(invoke_agents, "invoke_runtime", lambda role, payload, case_id: {"classification": classification})
        case = {"case_id": str(uuid.uuid4()), "message_id": "m1", "subject": "s", "body": "b", "sender": "a@b.com"}
        returned.append(invoke_agents.lambda_handler({"role": "classification", "case": case}))
        return escalated, labelled

    run.returned = returned
    return run


def test_a_payment_status_request_is_left_for_the_investigation(classify):
    escalated, labelled = classify("PAYMENT_STATUS_REQUEST")

    assert escalated == [] and NEEDS_ATTENTION not in labelled
    assert classify.returned[-1]["investigate"] is True


def test_a_remittance_advice_request_is_left_for_the_investigation(classify):
    escalated, labelled = classify("REMITTANCE_ADVICE_REQUEST")

    assert escalated == [] and NEEDS_ATTENTION not in labelled
    assert classify.returned[-1]["investigate"] is True


def test_a_status_request_that_also_mentions_new_bank_details_goes_to_a_person(classify):
    escalated, labelled = classify("PAYMENT_STATUS_REQUEST", account_change=True)

    assert [args[3] for args in escalated] == ["mentions a change of bank or payment details"]
    assert labelled[-1] == NEEDS_ATTENTION
    assert classify.returned[-1]["investigate"] is False


@pytest.mark.parametrize("request_type", ["INVOICE_DISPUTE", "PO_INVOICE_MISMATCH", "OTHER_PAYMENT_QUERY"])
def test_any_other_payment_request_goes_to_a_person_with_a_red_label(classify, request_type):
    escalated, labelled = classify(request_type)

    assert [args[3] for args in escalated] == [f"not a request the investigation can answer ({request_type})"]
    assert labelled[-1] == NEEDS_ATTENTION
    assert classify.returned[-1]["investigate"] is False


def test_a_mail_not_for_the_desk_goes_to_nobody(classify):
    escalated, labelled = classify("NOT_PAYMENT_RELATED", confidence=3, payment=False)

    assert escalated == [] and NEEDS_ATTENTION not in labelled
    assert classify.returned[-1]["investigate"] is False
