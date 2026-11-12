# PSC #394 Draft Builder Tool Lambda
# Developer source consolidated from tools/_shared/toolkit.py and tools/draftbuilder/handler.py.
# Only the now-local toolkit import was removed. Original developer sources are unchanged.

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


"""Draft Builder Tool.

Composes the supplier-facing reply from findings the tools have confirmed, and
applies export control to it.

On an export-controlled case the findings are dropped rather than caveated, and
nothing the caller supplied is echoed. query_summary is model-written free text
and has been observed carrying a purchase order number, so on that branch it
does not reach the supplier at all. The withheld detail goes to an internal
note instead.

This deliberately does not call the draft-builder API: that service ignores the
export-control flag and returns order detail regardless.
"""



# The payment details the supplier is sent for a paid invoice, as S/4HANA names
# them. The remittance details table is not confirmed yet, so it is not used.
PAYMENT_DETAIL_FIELDS = (
    "Supplier",
    "Amount Paid",
    "Payment Date",
    "Payment Method",
    "Reference #",
    "Originating Bank",
    "Receiving Bank",
)


def build_draft_reply(
    query_summary=None,
    root_cause=None,
    next_action=None,
    itar_controlled=None,
    transaction_id=None,
    transaction_details=None,
) -> dict:
    """Compose the supplier reply from confirmed evidence, respecting export control."""
    missing = [
        name
        for name, value in (
            ("query_summary", query_summary),
            ("root_cause", root_cause),
            ("next_action", next_action),
        )
        if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        return {"error": f"Required and missing: {', '.join(missing)}."}

    restricted = itar_controlled is True or (
        isinstance(itar_controlled, str) and itar_controlled.strip().lower() in {"yes", "true", "y", "1"}
    )

    lines = ["Dear Supplier,"]

    if restricted:
        # Nothing the caller supplied reaches the supplier on this branch. The
        # findings are dropped rather than caveated, and query_summary is not
        # echoed either: it is model-written free text and has been observed
        # carrying the purchase order number, which is exactly what must not
        # appear here. The detail goes to the internal note instead.
        lines += [
            "",
            "Thank you for your payment status query.",
            "",
            "Because this order is subject to export-control restrictions, we "
            "cannot set out the specifics of its status in writing here.",
            "",
            "Please contact your Boeing representative, who can discuss the "
            "details with you through the appropriate channel.",
        ]
    else:
        lines += [
            "",
            f"Thank you for your query regarding {query_summary.rstrip('.')}.",
            "",
            f"Following our investigation: {root_cause}",
            "",
            f"Next step: {next_action}",
        ]
        lines += _payment_lines(transaction_id, transaction_details)

    lines += ["", "We will confirm once this is resolved.", "", "Kind regards,", "Boeing Supplier Payments"]

    result = {
        "draft": "\n".join(lines),
        "itar_restricted": restricted,
        "detail_withheld": restricted,
    }
    if restricted:
        result["internal_note"] = (
            "Withheld from the supplier reply because the order is export "
            f"controlled. Root cause: {root_cause} Next action: {next_action}"
        )
        if isinstance(transaction_id, str) and transaction_id.strip():
            result["internal_note"] += f" Payment transaction: {transaction_id.strip()}."
    return result


def _payment_lines(transaction_id, transaction_details) -> list:
    """The payment transaction and its details, for a paid invoice.

    Only the confirmed fields are used, and only those with a value, so a
    partly filled record never prints an empty line.
    """
    lines = []
    if isinstance(transaction_id, str) and transaction_id.strip():
        lines.append(f"Payment transaction: {transaction_id.strip()}")
    if isinstance(transaction_details, dict):
        for name in PAYMENT_DETAIL_FIELDS:
            value = transaction_details.get(name)
            if value not in (None, ""):
                lines.append(f"{name}: {value}")
    return ["", "Payment details:", *lines] if lines else []


_TOOLS = {
    "build_draft_reply": (build_draft_reply, ()),
}


def lambda_handler(event, context):
    return dispatch(_TOOLS, event, context)
