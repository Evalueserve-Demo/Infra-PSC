"""Helpdesk Routing Tool.

Decides which helpdesk a case belongs to once its export control status is
known.

Nothing here calls an enterprise system. Routing an export-controlled case to
the wrong helpdesk is a compliance failure, so the decision is code.
"""

from toolkit import dispatch


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
