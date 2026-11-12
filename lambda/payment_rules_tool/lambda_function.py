"""#392 Payment Rules Tool Lambda. Developer handler and shared toolkit combined.
Original developer sources remain unchanged.
"""

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


# --- Developer paymentrules/handler.py ---
"""Payment Rules Tool.

The deterministic check on a supplier invoice ID: whether it follows the naming
convention.

Nothing here calls an enterprise system. This is a decision deliberately kept
out of the model, so that it is code that can be tested rather than a judgement
that can drift.
"""

import re


INVOICE_PATTERN = re.compile(r"^INV-\d{4,10}$")
PO_PATTERN = re.compile(r"^\d{10}$")


def validate_invoice_id(supplier_invoice_id: str) -> dict:
    """Check the supplier invoice ID against the Boeing naming convention."""
    value = supplier_invoice_id.strip()
    if INVOICE_PATTERN.match(value):
        return {"supplier_invoice_id": value, "valid": True, "convention": "INV-<digits>"}
    reason = (
        "Value looks like a purchase order number, not an invoice ID."
        if PO_PATTERN.match(value)
        else "Expected the form INV- followed by 4 to 10 digits."
    )
    return {
        "supplier_invoice_id": value,
        "valid": False,
        "convention": "INV-<digits>",
        "reason": reason,
    }


_TOOLS = {
    "validate_invoice_id": (validate_invoice_id, ("supplier_invoice_id",)),
}


def lambda_handler(event, context):
    return dispatch(_TOOLS, event, context)
