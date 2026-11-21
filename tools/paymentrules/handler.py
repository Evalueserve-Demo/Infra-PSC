"""Payment Rules Tool.

The deterministic check on a supplier invoice ID: whether it follows the naming
convention.

Nothing here calls an enterprise system. This is a decision deliberately kept
out of the model, so that it is code that can be tested rather than a judgement
that can drift.
"""

import re

from toolkit import dispatch

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
