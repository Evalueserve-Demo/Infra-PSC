"""S/4 Tool.

One container for what S/4HANA holds about an invoice, as two operations that
follow the agreed API contract:

    s4_get_invoice_reconciliation   API 1: reconciliation status, vendor, company
                                    code, purchasing organisation
    s4_get_payment_status           API 2: payment status, due date, and the
                                    payment transaction once settled

Both take the purchase order ID and the supplier invoice ID.

Neither API exists yet, so the responses come from the mock records below,
shaped exactly as the contract sheet shows and marked source: "mock". When the
real APIs arrive, only _call_api1 and _call_api2 change; the wrappers, and the
agent behind them, stay as they are.

The remittance details table inside transactionDetails is not confirmed yet. It
is not mocked, and nothing here reads it.
"""

import datetime

from toolkit import dispatch

DATE_FORMAT = "%m/%d/%Y"


def _day(offset: int) -> str:
    """A contract-format date relative to today, so the mock scenarios never go stale."""
    return (datetime.date.today() + datetime.timedelta(days=offset)).strftime(DATE_FORMAT)


def _mock_records() -> dict:
    """Mock S/4 records keyed by (purchase order, supplier invoice).

    Each covers one branch of the flow:
        INV-10234  paid, with payment transaction details
        INV-55678  overdue; supplier is in SPVR and blocked
        INV-20001  scheduled, not yet due
        INV-30001  reconciling, so the payment is still being sent
        INV-40001  rejected
        INV-50001  overdue; supplier is not in SPVR
        INV-60001  overdue; supplier is in SPVR and not blocked
    Any other pair is not in S/4HANA.
    """
    def api1(status, vendor):
        return {"invoiceStatus": status, "vendor": vendor,
                "companyCode": "1000", "purchaseOrganisation": "2000"}

    def api2(status, due, **extra):
        return {"paymentStatus": status, "currentPaymentTerms": due,
                "currentDueDate": due, **extra}

    return {
        ("4500123456", "INV-10234"): (
            api1("Reconciled", "001235"),
            api2("Paid", _day(-20), transaction="PMT1234", transactionDetails={
                "Supplier": "Global Parts Inc",
                "Amount Paid": "12,500.00",
                "Payment Date": _day(-18),
                "Payment Method": "ACH",
                "Reference #": "REF-778821",
                "Originating Bank": "First National Bank",
                "Receiving Bank": "Metro Commerce Bank",
            }),
        ),
        ("4500987654", "INV-55678"): (api1("Reconciled", "001234"), api2("Scheduled", _day(-15))),
        ("4500200001", "INV-20001"): (api1("Reconciled", "001235"), api2("Scheduled", _day(20))),
        ("4500300001", "INV-30001"): (api1("Reconciling", "001235"), api2("Sending", _day(10))),
        ("4500400001", "INV-40001"): (api1("Rejected", "001235"), api2("Rejected", None)),
        ("4500500001", "INV-50001"): (api1("Reconciled", "009999"), api2("Scheduled", _day(-10))),
        ("4500600001", "INV-60001"): (api1("Reconciled", "001236"), api2("Scheduled", _day(-5))),
    }


def _not_found(po_id: str, invoice_id: str) -> dict:
    return {"error": {"code": "NotFound",
                      "message": f"No S/4HANA record for invoice {invoice_id} on purchase order {po_id}."}}


def _call_api1(po_id: str, invoice_id: str) -> dict:
    """API 1, invoice validation and reconciliation. The single seam to replace."""
    record = _mock_records().get((po_id, invoice_id))
    return record[0] if record else _not_found(po_id, invoice_id)


def _call_api2(po_id: str, invoice_id: str) -> dict:
    """API 2, payment status determination. The single seam to replace."""
    record = _mock_records().get((po_id, invoice_id))
    return record[1] if record else _not_found(po_id, invoice_id)


def s4_get_invoice_reconciliation(po_id: str, supplier_invoice_id: str) -> dict:
    """Is the invoice in S/4HANA, and is it reconciled?"""
    po, invoice = po_id.strip(), supplier_invoice_id.strip()
    raw = _call_api1(po, invoice)
    if "error" in raw:
        return {"po_id": po, "supplier_invoice_id": invoice, "found": False,
                "present_in_s4": False, "source": "mock",
                "detail": raw["error"].get("message", "No record in S/4HANA.")}
    status = raw.get("invoiceStatus")
    return {
        "po_id": po,
        "supplier_invoice_id": invoice,
        "found": True,
        "present_in_s4": True,
        "invoice_status": status,
        # The status values are not final: Reconciled / Reconciling / Rejected, or
        # Reconciled / Not Reconciled. Only an exact "Reconciled" counts.
        "reconciled": str(status or "").strip().lower() == "reconciled",
        "vendor": raw.get("vendor"),
        "company_code": raw.get("companyCode"),
        "purchase_organisation": raw.get("purchaseOrganisation"),
        "source": "mock",
    }


def s4_get_payment_status(po_id: str, supplier_invoice_id: str) -> dict:
    """Is the invoice paid, when is it due, and what was the payment transaction?"""
    po, invoice = po_id.strip(), supplier_invoice_id.strip()
    raw = _call_api2(po, invoice)
    if "error" in raw:
        return {"po_id": po, "supplier_invoice_id": invoice, "found": False,
                "present_in_s4": False, "source": "mock",
                "detail": raw["error"].get("message", "No payment record in S/4HANA.")}
    due = raw.get("currentDueDate")
    passed, days = _due_date_passed(due)
    return {
        "po_id": po,
        "supplier_invoice_id": invoice,
        "found": True,
        "present_in_s4": True,
        "payment_status": raw.get("paymentStatus"),
        "amount_paid": str(raw.get("paymentStatus") or "").strip().lower() == "paid",
        "current_payment_terms": raw.get("currentPaymentTerms"),
        "current_due_date": due,
        "due_date_passed": passed,
        "days_overdue": days,
        "as_of": datetime.date.today().isoformat(),
        "transaction_id": raw.get("transaction"),
        "transaction_details": raw.get("transactionDetails"),
        "source": "mock",
    }


def _due_date_passed(due: str | None):
    """Has the due date passed, and by how many days?

    Computed here rather than left to the agent. An agent has no reliable
    clock, and comparing two dates is arithmetic, not judgement. Returning
    as_of alongside makes the comparison auditable. The format is MM/DD/YYYY,
    as in the contract sheet's samples; the format and time zone are not yet
    confirmed, so an unreadable date gives None rather than a guess.
    """
    if not due:
        return None, None
    try:
        date = datetime.datetime.strptime(due, DATE_FORMAT).date()
    except ValueError:
        return None, None
    delta = (datetime.date.today() - date).days
    return delta > 0, max(delta, 0)


_TOOLS = {
    "s4_get_invoice_reconciliation": (s4_get_invoice_reconciliation, ("po_id", "supplier_invoice_id")),
    "s4_get_payment_status": (s4_get_payment_status, ("po_id", "supplier_invoice_id")),
}


def lambda_handler(event, context):
    return dispatch(_TOOLS, event, context)
