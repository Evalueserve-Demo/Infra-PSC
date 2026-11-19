"""Tests for handing a mail to a person."""

import json
import pathlib
import re
from uuid import uuid4

import pytest

from payment_query_platform.escalation import MIN_CONFIDENCE, SUPPORTED_REQUESTS, escalate, investigates, review_reason
from payment_query_platform.storage.database import CaseRepository


class Cursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.log.append((sql, params))


class Database:
    def __init__(self):
        self.log = []

    def cursor(self):
        return Cursor(self.log)


def payment(request_type=SUPPORTED_REQUESTS[0], confidence=MIN_CONFIDENCE, account_change=False):
    return {"is_payment_related": True, "request_type": request_type, "confidence": confidence,
            "mentions_account_change": account_change}


class TestReviewReason:
    def test_a_status_request_at_the_threshold_is_investigated(self):
        assert review_reason(payment()) is None

    def test_a_payment_mail_below_the_threshold_goes_to_a_person(self):
        assert review_reason(payment(confidence=MIN_CONFIDENCE - 1)) == f"low-confidence classification ({MIN_CONFIDENCE - 1})"

    def test_a_missing_confidence_counts_as_unsure(self):
        assert "low-confidence" in review_reason({"is_payment_related": True, "request_type": SUPPORTED_REQUESTS[0]})

    @pytest.mark.parametrize("request_type", ["INVOICE_DISPUTE", "PO_INVOICE_MISMATCH", "OTHER_PAYMENT_QUERY"])
    def test_a_sure_payment_mail_the_investigation_cannot_answer_goes_to_a_person(self, request_type):
        assert review_reason(payment(request_type, confidence=10)) ==             f"not a request the investigation can answer ({request_type})"

    def test_a_remittance_advice_request_is_investigated(self):
        assert review_reason(payment("REMITTANCE_ADVICE_REQUEST", confidence=10)) is None

    def test_a_status_request_that_also_mentions_new_bank_details_goes_to_a_person(self):
        assert review_reason(payment(account_change=True, confidence=10)) == "mentions a change of bank or payment details"

    def test_an_older_classification_without_the_field_is_judged_as_before(self):
        assert review_reason({"is_payment_related": True, "request_type": SUPPORTED_REQUESTS[0], "confidence": 9}) is None

    def test_a_mail_that_is_not_for_the_desk_needs_nobody(self):
        assert review_reason({"is_payment_related": False, "request_type": "NOT_PAYMENT_RELATED", "confidence": 1}) is None


class TestInvestigates:
    def test_only_a_sure_status_request_without_an_account_change_is_investigated(self):
        assert investigates(payment())
        assert not investigates(payment(account_change=True))
        assert investigates(payment("REMITTANCE_ADVICE_REQUEST"))
        assert not investigates(payment("INVOICE_DISPUTE"))
        assert not investigates(payment(confidence=MIN_CONFIDENCE - 1))

    def test_a_mail_that_is_not_for_the_desk_is_not_investigated(self):
        assert not investigates({"is_payment_related": False, "request_type": "NOT_PAYMENT_RELATED", "confidence": 10})


def test_the_workflow_gate_reads_only_the_flag_the_lambda_decides():
    """The rule lives in code; the workflow must not carry a second copy of it."""
    terraform = (pathlib.Path(__file__).parent.parent / "220-step-function-2.tf").read_text(encoding="utf-8")
    state = terraform.split("IsPaymentRelated = {", 1)[1].split("ExtractIdentifiers = {", 1)[0]
    state = re.sub(r"\s+", " ", re.sub(r"#[^\n]*", "", state))

    # One choice, on the flag the Lambda sets. Nothing else decides who is investigated.
    assert state.count("Variable") == 1
    assert re.search(r'Variable\s*=\s*"\$\.investigate"\s*BooleanEquals\s*=\s*true\s*Next\s*=\s*"ExtractIdentifiers"', state)
    assert re.search(r'Default\s*=\s*"NotInvestigated"', state)


class TestEscalate:
    def test_the_mail_and_its_case_are_marked(self):
        database = Database()
        escalate(database, "msg-1", "case-1", "low-confidence classification (2)", {"confidence": 2})
        assert len(database.log) == 2
        assert "NEEDS_ATTENTION" in database.log[0][0] and database.log[0][1] == ("msg-1",)
        reason, case_id, details = database.log[1][1]
        assert (reason, case_id) == ("low-confidence classification (2)", "case-1")
        assert details.obj == {"confidence": 2, "graph_message_id": "msg-1"}

    def test_a_mail_with_no_case_is_still_marked(self):
        database = Database()
        escalate(database, "msg-1", None, "no case", {})
        assert len(database.log) == 1


class TestOutOfScope:
    def test_only_a_case_still_at_new_is_finished(self):
        database, case_id = Database(), uuid4()
        CaseRepository(database).mark_out_of_scope(str(case_id))
        sql, params = database.log[0]
        assert "OUT_OF_SCOPE" in sql and "case_status = 'NEW'" in sql
        assert params == (case_id,)
