"""PSC #390 SAP S/4HANA Tool Lambda.

Consolidates the developer's tools/s4hana/handler.py and tools/_shared/toolkit.py,
while preserving the company's original #390 entry behavior as a fallback.
Gateway tool calls use developer operations s4_get_invoice_reconciliation and
s4_get_payment_status. Legacy calls without tool metadata keep original behavior.
No database access or schema changes. Mock SAP responses remain mock.
"""

# ===== Original company handler (retained, renamed for fallback) =====

import json
import logging
import os
from datetime import datetime as _company_datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

INTEGRATION_MODE = os.environ.get(
    "S4HANA_INTEGRATION_MODE", "DISABLED"
)


def response(status, case_id, message_id, invoice_id, error_code=None):
    result = {
        "status": status,
        "system": "SAP_S4HANA",
        "case_id": case_id,
        "message_id": message_id,
        "invoice_id": invoice_id,
        "payment_status": None,
        "payment_date": None,
        "payment_reference": None,
        "amount": None,
        "currency": None,
        "error_code": error_code,
        "checked_at": _company_datetime.now(timezone.utc).isoformat()
    }

    return result


def _company_legacy_lambda_handler(event, context):
    if not isinstance(event, dict):
        return response(
            "INVALID_REQUEST", None, None, None,
            "EVENT_MUST_BE_OBJECT"
        )

    # Supports direct Lambda invocation and a Gateway
    # payload containing the input under "body".
    payload = event.get("body", event)

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return response(
                "INVALID_REQUEST", None, None, None,
                "INVALID_JSON_BODY"
            )

    if not isinstance(payload, dict):
        return response(
            "INVALID_REQUEST", None, None, None,
            "INVALID_PAYLOAD"
        )

    case_id = payload.get("case_id")
    message_id = payload.get("message_id")
    invoice_id = payload.get("invoice_id")

    if not all(
        isinstance(value, str) and value.strip()
        for value in (case_id, message_id, invoice_id)
    ):
        return response(
            "VALIDATION_FAILED",
            case_id,
            message_id,
            invoice_id,
            "MISSING_REQUIRED_IDENTIFIERS"
        )

    case_id = case_id.strip()
    message_id = message_id.strip()
    invoice_id = invoice_id.strip()

    if INTEGRATION_MODE != "LIVE":
        logger.info(
            "SAP S/4HANA integration disabled for case_id=%s",
            case_id
        )

        return response(
            "INTEGRATION_NOT_CONFIGURED",
            case_id,
            message_id,
            invoice_id,
            "SAP_API_NOT_ENABLED"
        )

    # Fail closed until the approved SAP API contract,
    # credentials, authorization, and response mapping
    # have been implemented.
    return response(
        "INTEGRATION_NOT_CONFIGURED",
        case_id,
        message_id,
        invoice_id,
        "SAP_API_IMPLEMENTATION_PENDING"
    )

# ===== Developer shared toolkit (inlined, unmodified logic) =====
"""Shared plumbing for the tool Lambdas.

Every tool container serves several operations from one function. The Gateway
names the operation in the client context as "<target>___<tool>", which
resolve_tool_name unpacks, and dispatch routes.

call_api is here because the API-backed tools all talk to an enterprise system
the same way: a GET or POST over HTTPS, a short timeout, and a readable error
rather than a stack trace. When a system becomes real this is where its
credentials and network path will go.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_SECONDS = 20
MAX_ARG_BYTES = 8000


def resolve_tool_name(context) -> str:
    client_context = getattr(context, "client_context", None)
    custom = getattr(client_context, "custom", None) if client_context else None
    raw = (custom or {}).get("bedrockAgentCoreToolName", "") if custom else ""
    return raw.split("___", 1)[1] if "___" in raw else raw


def call_api(url: str, method: str = "GET", body: dict | None = None) -> dict:
    """Call an enterprise system and return its JSON, or a readable error.

    A 404 is an answer, not a failure: "no payment block exists" is exactly
    what the agent needs to know, so the body is returned rather than raised.
    """
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:
            return {"error": {"code": str(exc.code), "message": exc.reason}}
    except urllib.error.URLError as exc:
        return {"error": {"code": "Unreachable", "message": f"{url} could not be reached: {exc.reason}"}}
    except json.JSONDecodeError:
        return {"error": {"code": "BadResponse", "message": f"{url} did not return JSON."}}


def dispatch(table: dict, event, context):
    """Route one Gateway tool call to the function that serves it."""
    name = resolve_tool_name(context)
    entry = table.get(name)
    if entry is None:
        return {"error": f"Unknown tool: {name!r}"}
    if not isinstance(event, dict):
        return {"error": "Expected a JSON object of tool arguments."}
    if len(str(event)) > MAX_ARG_BYTES:
        return {"error": "Arguments exceed the size limit."}

    func, required = entry
    for field in required:
        value = event.get(field)
        if not isinstance(value, str) or not value.strip():
            return {"error": f"{field} must be a non-empty string."}
    return func(**{k: v for k, v in event.items() if k != "tool"})

# ===== Developer S4HANA handler (inlined; dispatcher wired locally) =====
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
    """Gateway tool dispatch; preserve legacy company invocation as fallback."""
    name = resolve_tool_name(context)
    if not name and isinstance(event, dict):
        name = event.get("tool") or event.get("tool_name") or ""
        if isinstance(name, str) and "___" in name:
            name = name.split("___", 1)[1]
    if name in _TOOLS:
        if not isinstance(event, dict):
            return {"error": "Expected a JSON object of tool arguments."}
        if len(str(event)) > MAX_ARG_BYTES:
            return {"error": "Arguments exceed the size limit."}
        func, required = _TOOLS[name]
        for field in required:
            value = event.get(field)
            if not isinstance(value, str) or not value.strip():
                return {"error": f"{field} must be a non-empty string."}
        return func(**{k: v for k, v in event.items() if k not in ("tool", "tool_name")})
    if name:
        return {"error": f"Unknown tool: {name!r}"}
    return _company_legacy_lambda_handler(event, context)
