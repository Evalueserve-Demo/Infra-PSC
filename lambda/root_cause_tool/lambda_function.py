"""#393 Root Cause Map Tool Lambda. Original developer logic and toolkit combined."""

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


# --- Developer tools/rootcause/handler.py ---
"""Helpdesk Routing Tool.

Decides which helpdesk a case belongs to once its export control status is
known.

Nothing here calls an enterprise system. Routing an export-controlled case to
the wrong helpdesk is a compliance failure, so the decision is code.
"""



def route_helpdesk(itar_flag=None) -> dict:
    """Route to the US helpdesk when the ITAR flag is set, otherwise TCS."""
    if isinstance(itar_flag, str):
        is_itar = itar_flag.strip().lower() in {"yes", "true", "y", "1"}
        known = itar_flag.strip() != ""
    elif isinstance(itar_flag, bool):
        is_itar, known = itar_flag, True
    else:
        is_itar, known = False, False

    if not known:
        return {
            "helpdesk": "TCS Helpdesk",
            "itar_known": False,
            "reason": "No ITAR flag was supplied; defaulting to TCS. Confirm before sending.",
        }
    return {
        "helpdesk": "US Helpdesk" if is_itar else "TCS Helpdesk",
        "itar_known": True,
        "reason": "ITAR controlled." if is_itar else "Not ITAR controlled.",
    }


_TOOLS = {
    "route_helpdesk": (route_helpdesk, ()),
}


def lambda_handler(event, context):
    return dispatch(_TOOLS, event, context)
