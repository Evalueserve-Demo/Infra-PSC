"""Tests for the S/4HANA and SPVR tools, and the pieces of the investigation that
carry their results to the supplier.

The three APIs do not exist yet, so these run against the mock records. Each
mock invoice covers one branch of the flow; the tests walk the same chain the
agent does, passing what one tool returns into the next.
"""

import datetime

import pytest

from payment_query_platform.investigation.invoke_agent import (
    build_question,
    combined_reply,
    itar_flag,
    itar_routed,
)
from payment_query_platform.investigation.schema import Investigation
import s4hana_tool as s4
from s4hana_tool import s4_get_invoice_reconciliation, s4_get_payment_status
from spvr_tool import BLOCK_FLAGS, best_code_from_vendor, spvr_get_supplier_block

# (purchase order, invoice) for each branch of the flow.
PAID = ("4500123456", "INV-10234")
BLOCKED = ("4500987654", "INV-55678")
NOT_YET_DUE = ("4500200001", "INV-20001")
SENDING = ("4500300001", "INV-30001")
REJECTED = ("4500400001", "INV-40001")
NOT_IN_SPVR = ("4500500001", "INV-50001")
NOT_BLOCKED = ("4500600001", "INV-60001")


def supplier_check(pair):
    """The yellow band as the agent runs it: vendor and codes from API 1 into API 3."""
    invoice = s4_get_invoice_reconciliation(*pair)
    return spvr_get_supplier_block(
        invoice["vendor"], invoice["company_code"], invoice["purchase_organisation"]
    )


class TestInvoiceReconciliation:
    def test_returns_the_contract_fields(self):
        result = s4_get_invoice_reconciliation(*BLOCKED)
        assert result["found"] is True
        assert result["invoice_status"] == "Reconciled"
        assert result["vendor"] == "001234"
        assert result["company_code"] and result["purchase_organisation"]

    @pytest.mark.parametrize("status, reconciled", [
        ("Reconciled", True),
        ("Reconciling", False),
        ("Rejected", False),
        ("Not Reconciled", False),
    ])
    def test_both_status_vocabularies_are_understood(self, monkeypatch, status, reconciled):
        monkeypatch.setattr(s4, "_call_api1", lambda po, inv: {"invoiceStatus": status, "vendor": "001234"})
        assert s4_get_invoice_reconciliation(*BLOCKED)["reconciled"] is reconciled

    def test_an_unknown_invoice_is_not_found_rather_than_an_error(self):
        result = s4_get_invoice_reconciliation("4500000000", "INV-99999")
        assert result["found"] is False
        assert result["present_in_s4"] is False

    def test_the_purchase_order_must_match_the_invoice(self):
        assert s4_get_invoice_reconciliation("4500000000", BLOCKED[1])["found"] is False


class TestPaymentStatus:
    def test_a_paid_invoice_returns_its_transaction(self):
        result = s4_get_payment_status(*PAID)
        assert result["amount_paid"] is True
        assert result["transaction_id"] == "PMT1234"
        assert result["transaction_details"]["Amount Paid"]

    def test_an_unpaid_invoice_has_no_transaction(self):
        result = s4_get_payment_status(*BLOCKED)
        assert result["amount_paid"] is False
        assert result["transaction_id"] is None

    def test_a_past_due_date_is_overdue(self):
        result = s4_get_payment_status(*BLOCKED)
        assert result["due_date_passed"] is True
        assert result["days_overdue"] == 15

    def test_a_future_due_date_is_not_overdue(self):
        result = s4_get_payment_status(*NOT_YET_DUE)
        assert result["payment_status"] == "Scheduled"
        assert result["due_date_passed"] is False
        assert result["days_overdue"] == 0

    def test_the_date_is_compared_against_today(self):
        assert s4_get_payment_status(*BLOCKED)["as_of"] == datetime.date.today().isoformat()

    @pytest.mark.parametrize("pair, status", [(SENDING, "Sending"), (REJECTED, "Rejected")])
    def test_the_other_statuses_come_back_as_reported(self, pair, status):
        assert s4_get_payment_status(*pair)["payment_status"] == status

    def test_a_missing_due_date_is_unknown_not_overdue(self):
        result = s4_get_payment_status(*REJECTED)
        assert result["due_date_passed"] is None
        assert result["days_overdue"] is None

    def test_an_unreadable_due_date_is_unknown_not_overdue(self, monkeypatch):
        monkeypatch.setattr(s4, "_call_api2", lambda po, inv: {"paymentStatus": "Scheduled", "currentDueDate": "2026-09-01"})
        assert s4_get_payment_status(*BLOCKED)["due_date_passed"] is None

    def test_an_unknown_invoice_is_not_found(self):
        assert s4_get_payment_status("4500000000", "INV-99999")["found"] is False


class TestSupplierCheck:
    def test_the_best_code_comes_from_the_vendor_code(self):
        assert best_code_from_vendor("001234") == "BE1234"

    def test_a_blocked_supplier_names_the_block(self):
        result = supplier_check(BLOCKED)
        assert result["in_spvr"] is True
        assert result["blocked"] is True
        assert result["best_code"] == "BE1234"
        assert result["active_blocks"] == ["waitingOnW8W9Block"]

    def test_a_registered_supplier_with_no_block_is_not_blocked(self):
        result = supplier_check(NOT_BLOCKED)
        assert result["in_spvr"] is True
        assert result["blocked"] is False
        assert result["active_blocks"] == []

    def test_an_unregistered_supplier_is_not_in_spvr(self):
        result = supplier_check(NOT_IN_SPVR)
        assert result["found"] is False
        assert result["in_spvr"] is False
        assert "blocked" not in result

    def test_every_flag_is_reported(self):
        assert set(supplier_check(BLOCKED)["flags"]) == set(BLOCK_FLAGS)

    def test_the_company_code_is_part_of_the_lookup(self):
        assert spvr_get_supplier_block("001234", "9999", "2000")["in_spvr"] is False


class TestEveryMockBranch:
    """Each mock invoice reaches the step the flow says it should."""

    def test_paid_stops_at_the_transaction(self):
        assert s4_get_payment_status(*PAID)["amount_paid"] is True

    def test_not_yet_due_stops_before_spvr(self):
        assert s4_get_payment_status(*NOT_YET_DUE)["due_date_passed"] is False

    @pytest.mark.parametrize("pair", [BLOCKED, NOT_IN_SPVR, NOT_BLOCKED])
    def test_overdue_invoices_reach_the_supplier_check(self, pair):
        status = s4_get_payment_status(*pair)
        assert status["amount_paid"] is False
        assert status["due_date_passed"] is True


class TestToolDispatch:
    class Context:
        class client_context:
            custom = {"bedrockAgentCoreToolName": "s4hana___s4_get_payment_status"}

    def test_both_identifiers_are_required(self):
        result = s4.lambda_handler({"supplier_invoice_id": "INV-55678"}, self.Context)
        assert "po_id" in result["error"]

    def test_the_gateway_call_reaches_the_tool(self):
        result = s4.lambda_handler({"po_id": BLOCKED[0], "supplier_invoice_id": BLOCKED[1]}, self.Context)
        assert result["found"] is True


class TestInvestigationResult:
    """The model fills what applies and reports the rest as null; a step it never reached is not an error."""

    REQUIRED = {"outcome": "NOT_FOUND", "draft_reply": "x", "helpdesk": "TCS Helpdesk", "helpdesk_provisional": False}

    @pytest.mark.parametrize("field", ["spvr_active_blocks", "evidence", "unavailable_tools"])
    def test_a_null_list_is_read_as_empty(self, field):
        assert getattr(Investigation(**self.REQUIRED, **{field: None}), field) == []

    def test_a_list_that_was_filled_is_kept(self):
        result = Investigation(**self.REQUIRED, spvr_active_blocks=["waitingOnW8W9Block"])
        assert result.spvr_active_blocks == ["waitingOnW8W9Block"]


class TestItarInput:
    """The ITAR flag comes from the ticket, and the case value decides the redaction."""

    @pytest.mark.parametrize("value, expected", [
        ("Yes", True), ("yes", True), ("Y", True), (True, True),
        ("No", False), ("false", False), (False, False),
        (None, None), ("", None), ("  ", None),
    ])
    def test_the_ticket_flag_is_read(self, value, expected):
        assert itar_flag({"itar_flag": value}) is expected

    def test_a_case_with_no_flag_says_so(self):
        assert itar_flag({}) is None

    def test_the_question_carries_the_flag_and_the_purchase_order(self):
        case = {"itar_flag": "Yes", "sender": "a@b.com", "subject": "s", "body": "b"}
        question = build_question(case, "INV-55678", "4500987654")
        assert "Purchase order: 4500987654" in question
        assert "ITAR flag from the ticket: Yes" in question

    def test_the_question_says_when_something_was_not_provided(self):
        question = build_question({"sender": "a@b.com"}, "INV-55678", None)
        assert "Purchase order: not provided" in question
        assert "ITAR flag from the ticket: not provided" in question

    def test_a_not_found_invoice_goes_to_tcs_whatever_the_flag_says(self):
        assert itar_routed({"itar_controlled": True, "outcome": "NOT_FOUND"}) is False

    def test_a_controlled_invoice_routes_by_its_flag(self):
        assert itar_routed({"itar_controlled": True, "outcome": "OVERDUE"}) is True
        assert itar_routed({"itar_controlled": False, "outcome": "OVERDUE"}) is False
        assert itar_routed({"outcome": "OVERDUE"}) is False


class TestCombinedReply:
    PAID_RESULT = {
        "outcome": "PAID",
        "transaction_id": "PMT1234",
        "transaction_details": {"amount_paid": "12,500.00", "payment_method": "ACH",
                                "reference": "REF-778821", "receiving_bank": "Metro Commerce Bank"},
    }

    def reply(self, result, **item):
        return combined_reply([{"invoice_number": "INV-10234", "result": result, **item}])

    def test_a_paid_invoice_carries_its_payment_details(self):
        reply = self.reply(self.PAID_RESULT)
        for value in ("PMT1234", "12,500.00", "ACH", "REF-778821", "Metro Commerce Bank"):
            assert value in reply

    def test_an_export_controlled_paid_invoice_withholds_them(self):
        reply = self.reply({**self.PAID_RESULT, "itar_controlled": True})
        for value in ("PMT1234", "12,500.00", "REF-778821"):
            assert value not in reply

    @pytest.mark.parametrize("outcome", ["PAID", "SCHEDULED", "OVERDUE", "REJECTED"])
    def test_an_export_controlled_reply_does_not_claim_the_invoice_is_on_hold(self, outcome):
        """Whatever the status, the reply must not say it: a paid invoice is not on hold."""
        reply = self.reply({"outcome": outcome, "itar_controlled": True})
        assert "on hold" not in reply.lower()
        assert "export-control" in reply

    def test_a_scheduled_invoice_gives_its_due_date(self):
        assert "Due date: 12/30/2026" in self.reply({"outcome": "SCHEDULED", "due_date": "12/30/2026"})

    def test_a_not_found_invoice_is_not_redacted_as_export_controlled(self):
        reply = self.reply({"outcome": "NOT_FOUND", "itar_controlled": True})
        assert "export-control" not in reply
        assert "could not locate" in reply
