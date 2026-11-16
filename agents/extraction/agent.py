"""PSC Extraction Agent business contract (no external dependencies).

Same shape as the payment_info / IdentifierPair schema.
"""

VALID_SOURCES = {"EMAIL_SUBJECT", "EMAIL_BODY", "ATTACHMENT"}


def validate_extraction(result: dict) -> dict:
    if not isinstance(result, dict) or not isinstance(result.get("payment_info"), list):
        raise ValueError("Extraction must contain a payment_info list")
    pairs = []
    for item in result["payment_info"]:
        if not isinstance(item, dict):
            raise ValueError("Each payment_info entry must be an object")
        po = item.get("po_number")
        invoice = item.get("invoice_number")
        source = item.get("found_in")
        if po is not None and not isinstance(po, str):
            raise ValueError("po_number must be a string or null")
        if invoice is not None and not isinstance(invoice, str):
            raise ValueError("invoice_number must be a string or null")
        if po is None and invoice is None:
            raise ValueError("Identifier pair must contain at least one identifier")
        if source not in VALID_SOURCES:
            raise ValueError("found_in must be EMAIL_SUBJECT, EMAIL_BODY or ATTACHMENT")
        pairs.append({"po_number": po, "invoice_number": invoice, "found_in": source})
    return {"payment_info": pairs}
