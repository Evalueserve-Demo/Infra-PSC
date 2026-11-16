"""PSC #391 SPVR Tool Lambda. The shared tool helpers and the handler, in one file."""

# ===== Shared tool helpers =====
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


# ===== SPVR handler =====
"""The SPVR supplier register.

A separate system from S/4HANA, so a separate Gateway target. One operation,
the yellow band of the flow, which runs only when an invoice is overdue and
unpaid:

    spvr_get_supplier_block   API 3: is the supplier registered, and is it blocked

The contract takes a best code, a company code and a purchasing organisation.
The best code is derived here from the vendor code S/4HANA returned, so the
agent passes the S/4 values straight through.

The API does not exist yet, so the register is held here and every response
carries source: "mock". Replacing it means rewriting _call_api3 only.
"""


# The block flags API 3 returns, in the order the contract sheet lists them.
BLOCK_FLAGS = (
    "companyCodePostingBlock",
    "companyCodePaymentBlock",
    "purchaseOrganisationPurchasingBlock",
    "bankruptcyBlock",
    "buyerFurnishedEquipmentBlock",
    "courtOrderBlock",
    "debitBalanceBlock",
    "profileInactive",
    "lawsuitPendingBlock",
    "paymentRestrictedBlock",
    "waitingOnW8W9Block",
    "thirdPartyRiskVisibilityBlock",
)


def _entry(*active: str) -> dict:
    """An API 3 response: every flag false except those named, and block set when any is."""
    flags = {name: name in active for name in BLOCK_FLAGS}
    return {"block": any(flags.values()), **flags}


# Keyed by (best code, company code, purchasing organisation), the contract's inputs.
REGISTER = {
    ("BE1234", "1000", "2000"): _entry("waitingOnW8W9Block"),
    ("BE1235", "1000", "2000"): _entry(),
    ("BE1236", "1000", "2000"): _entry(),
}


def best_code_from_vendor(vendor: str) -> str:
    """Vendor code to best code, for example 001234 to BE1234.

    The rule comes from one example in the contract sheet and is not confirmed:
    "BE" plus the last four digits. If it turns out to be a lookup, this is the
    only place that changes.
    """
    return f"BE{vendor.strip()[-4:]}"


def _call_api3(best_code: str, company_code: str, purchase_organisation: str) -> dict:
    """API 3, supplier and SPVR investigation. The single seam to replace."""
    entry = REGISTER.get((best_code, company_code, purchase_organisation))
    if entry is None:
        return {"error": {"code": "NotFound",
                          "message": f"Best code {best_code} is not in SPVR for company code "
                                     f"{company_code} and purchasing organisation {purchase_organisation}."}}
    return entry


def spvr_get_supplier_block(vendor: str, company_code: str, purchase_organisation: str) -> dict:
    """Is the supplier in SPVR, and is it blocked?"""
    best_code = best_code_from_vendor(vendor)
    raw = _call_api3(best_code, company_code.strip(), purchase_organisation.strip())
    if "error" in raw:
        return {"vendor": vendor, "best_code": best_code, "found": False, "in_spvr": False,
                "source": "mock", "detail": raw["error"].get("message", "Supplier not found in SPVR.")}
    flags = {name: bool(raw.get(name)) for name in BLOCK_FLAGS}
    return {
        "vendor": vendor,
        "best_code": best_code,
        "found": True,
        "in_spvr": True,
        "blocked": bool(raw.get("block")),
        "active_blocks": [name for name, on in flags.items() if on],
        "flags": flags,
        "source": "mock",
    }


_TOOLS = {
    "spvr_get_supplier_block": (spvr_get_supplier_block, ("vendor", "company_code", "purchase_organisation")),
}


def lambda_handler(event, context):
    return dispatch(_TOOLS, event, context)
