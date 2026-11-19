"""Tests for the deterministic tool containers.

These are the decisions deliberately kept out of the model: the invoice-format
check, helpdesk routing, and the export control rules governing what a supplier
may be told. The agent orchestrates; these tools decide. So this is where the
tests belong.

The S/4HANA and SPVR tools are in test_apis.py.
"""

import pytest

from tools.draftbuilder.handler import build_draft_reply
from tools.paymentrules.handler import validate_invoice_id
from tools.rootcause.handler import route_helpdesk


class TestValidateInvoiceId:
    @pytest.mark.parametrize("value", ["INV-10234", "INV-55678", "INV-1001", " INV-10234 "])
    def test_accepts_the_convention(self, value):
        assert validate_invoice_id(value)["valid"] is True

    @pytest.mark.parametrize("value", ["12345", "INVOICE-123", "INV-", "INV-12", "inv 10234"])
    def test_rejects_anything_else(self, value):
        assert validate_invoice_id(value)["valid"] is False

    def test_a_purchase_order_is_named_as_such(self):
        """The commonest supplier mistake deserves a better message than 'invalid'."""
        result = validate_invoice_id("4500123456")
        assert result["valid"] is False
        assert "purchase order" in result["reason"].lower()


class TestDraftReply:
    FINDINGS = {
        "query_summary": "payment status for INV-55678",
        "root_cause": "Goods receipt GR-97550 shows 95 of 100 units received against PO 4500987654",
        "next_action": "Confirm the short delivery with the warehouse, then post the goods receipt",
    }

    # Anything that would disclose the order to a supplier.
    SENSITIVE = ("4500987654", "GR-97550", "95", "100", "Q1", "warehouse", "goods receipt")

    def test_an_ordinary_reply_carries_the_findings(self):
        result = build_draft_reply(**self.FINDINGS, itar_controlled=False)
        assert result["itar_restricted"] is False
        assert self.FINDINGS["root_cause"] in result["draft"]
        assert self.FINDINGS["next_action"] in result["draft"]

    def test_an_export_controlled_reply_withholds_every_detail(self):
        """The whole point: redaction must remove the detail, not caveat it."""
        result = build_draft_reply(**self.FINDINGS, itar_controlled=True)
        draft = result["draft"].lower()
        for term in self.SENSITIVE:
            assert term.lower() not in draft, f"{term!r} leaked into the supplier reply"

    def test_a_leaky_query_summary_cannot_reach_the_supplier(self):
        """query_summary is model-written free text, so it is not trustworthy.

        Observed in a live run: the model wrote the purchase order number into
        query_summary, and it reached an ITAR-restricted supplier draft through
        a branch that had dropped every other detail.
        """
        result = build_draft_reply(
            query_summary="Global Parts Inc queried INV-55678 against PO 4500987654",
            root_cause=self.FINDINGS["root_cause"],
            next_action=self.FINDINGS["next_action"],
            itar_controlled=True,
        )
        assert "4500987654" not in result["draft"]

    def test_an_export_controlled_reply_does_not_claim_the_invoice_is_on_hold(self):
        """The same reply goes out for a paid invoice, so it must not state a status."""
        draft = build_draft_reply(**self.FINDINGS, itar_controlled=True)["draft"]
        assert "on hold" not in draft.lower()
        assert "export-control" in draft

    def test_the_withheld_detail_is_kept_internally(self):
        """Redacted from the supplier is not the same as discarded."""
        result = build_draft_reply(**self.FINDINGS, itar_controlled=True)
        assert self.FINDINGS["root_cause"] in result["internal_note"]
        assert result["detail_withheld"] is True

    @pytest.mark.parametrize("flag", ["yes", "true", "Y", "1", True])
    def test_truthy_itar_values_all_restrict(self, flag):
        """The flag arrives from a model, so it may be a string or a bool."""
        assert build_draft_reply(**self.FINDINGS, itar_controlled=flag)["itar_restricted"] is True

    PAYMENT = {
        "transaction_id": "PMT1234",
        "transaction_details": {
            "Supplier": "Global Parts Inc",
            "Amount Paid": "12,500.00",
            "Payment Date": "10/10/2026",
            "Payment Method": "ACH",
            "Reference #": "REF-778821",
            "Originating Bank": "First National Bank",
            "Receiving Bank": "Metro Commerce Bank",
            "Remittance Details Table": [{"Payment Request": "PR-1"}],
        },
    }

    def test_a_paid_reply_carries_the_payment_details(self):
        draft = build_draft_reply(**self.FINDINGS, itar_controlled=False, **self.PAYMENT)["draft"]
        assert "PMT1234" in draft
        for value in ("Global Parts Inc", "12,500.00", "10/10/2026", "ACH", "REF-778821",
                      "First National Bank", "Metro Commerce Bank"):
            assert value in draft

    def test_the_unconfirmed_remittance_table_is_not_printed(self):
        draft = build_draft_reply(**self.FINDINGS, itar_controlled=False, **self.PAYMENT)["draft"]
        assert "PR-1" not in draft
        assert "Remittance" not in draft

    def test_a_partly_filled_record_prints_no_empty_lines(self):
        details = {"Amount Paid": "12,500.00", "Payment Method": "", "Reference #": None}
        draft = build_draft_reply(**self.FINDINGS, transaction_details=details)["draft"]
        assert "Amount Paid: 12,500.00" in draft
        assert "Payment Method" not in draft
        assert "Reference #" not in draft

    def test_a_reply_without_a_transaction_has_no_payment_section(self):
        assert "Payment details" not in build_draft_reply(**self.FINDINGS)["draft"]

    def test_an_export_controlled_reply_withholds_the_payment_details(self):
        result = build_draft_reply(**self.FINDINGS, itar_controlled=True, **self.PAYMENT)
        for value in ("PMT1234", "12,500.00", "REF-778821", "First National Bank"):
            assert value not in result["draft"]
        assert "PMT1234" in result["internal_note"]

    def test_incomplete_findings_produce_no_draft(self):
        result = build_draft_reply(query_summary="x", root_cause="", next_action="y")
        assert "error" in result
        assert "draft" not in result


class TestHelpdeskRouting:
    def test_controlled_items_stay_with_the_us_helpdesk(self):
        assert route_helpdesk(True)["helpdesk"] == "US Helpdesk"

    def test_uncontrolled_items_go_to_tcs(self):
        assert route_helpdesk(False)["helpdesk"] == "TCS Helpdesk"

    @pytest.mark.parametrize("flag", ["yes", "true", "Y", "1"])
    def test_string_flags_are_understood(self, flag):
        assert route_helpdesk(flag)["helpdesk"] == "US Helpdesk"

    def test_an_absent_flag_is_marked_unconfirmed(self):
        """Defaulting to TCS is only safe if the caller is told it was a default."""
        result = route_helpdesk(None)
        assert result["itar_known"] is False
        assert "confirm" in result["reason"].lower()
