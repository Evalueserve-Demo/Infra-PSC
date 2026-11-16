"""PSC #270 Identifier Validation Lambda.

Checks the invoice and PO numbers found in a mail, using the same rules as the
payment rules tool. It does not touch the database.
"""
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

MAX_INVOICES = int(os.getenv("MAX_INVOICES", "25"))
MAX_IDENTIFIER_LENGTH = int(os.getenv("MAX_IDENTIFIER_LENGTH", "100"))
INVOICE_PATTERN = re.compile(r"^INV-\d{4,10}$")
PO_PATTERN = re.compile(r"^\d{10}$")
CASE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
SOURCES = {"EMAIL_BODY", "SUBJECT", "ATTACHMENT", "HUMAN"}
SOURCE_ALIASES = {"EMAIL_SUBJECT": "SUBJECT"}


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _invoice(value: Any) -> str:
    return _string(value).upper()


def _extract(event: dict):
    """Take the invoice numbers from invoice_ids, invoice_id or payment_info."""
    if "invoice_ids" in event:
        if not isinstance(event["invoice_ids"], list):
            return [], [], ["invoice_ids must be a list"]
        return event["invoice_ids"], [], []
    if "invoice_id" in event:
        return [event["invoice_id"]], [], []
    payment_info = event.get("payment_info")
    if payment_info is None:
        extraction = event.get("extraction")
        if isinstance(extraction, dict):
            payment_info = extraction.get("payment_info")
    if payment_info is None:
        return [], [], []
    if not isinstance(payment_info, list):
        return [], [], ["payment_info must be a list"]
    invoices, pairs, errors = [], [], []
    for pair in payment_info:
        if not isinstance(pair, dict):
            errors.append("payment_info entries must be objects")
            continue
        invoice = pair.get("invoice_number")
        po = pair.get("po_number")
        source = SOURCE_ALIASES.get(pair.get("found_in"), pair.get("found_in"))
        if invoice is not None:
            invoices.append(invoice)
        pairs.append({"invoice_number": invoice, "po_number": po, "source": source})
    return invoices, pairs, errors


def validate_identifiers(event: dict) -> dict:
    if not isinstance(event, dict):
        raise ValueError("Input must be a JSON object")
    case_id = _string(event.get("case_id"))
    message_id = _string(event.get("message_id"))
    errors = []
    if not case_id:
        errors.append("case_id is required")
    elif len(case_id) > MAX_IDENTIFIER_LENGTH or not CASE_PATTERN.fullmatch(case_id):
        errors.append("case_id format is invalid")
    if not message_id:
        errors.append("message_id is required")
    elif len(message_id) > MAX_IDENTIFIER_LENGTH:
        errors.append("message_id is too long")

    invoice_values, pairs, extract_errors = _extract(event)
    errors.extend(extract_errors)
    if len(invoice_values) > MAX_INVOICES:
        errors.append(f"Maximum {MAX_INVOICES} invoice identifiers allowed")
    valid, invalid, seen = [], [], set()
    for raw in invoice_values[:MAX_INVOICES]:
        value = _invoice(raw)
        if (not INVOICE_PATTERN.fullmatch(value)
                or len(value) > min(MAX_IDENTIFIER_LENGTH, 16)):
            invalid.append(str(raw)[:MAX_IDENTIFIER_LENGTH])
        elif value not in seen:
            seen.add(value)
            valid.append(value)
    if invalid:
        errors.append("One or more invoice identifiers are invalid")
    if not valid:
        errors.append("At least one valid invoice identifier is required")

    validated_pairs = []
    for pair in pairs:
        invoice = _invoice(pair["invoice_number"])
        po = _string(pair["po_number"])
        source = pair["source"]
        pair_errors = []
        if invoice and not INVOICE_PATTERN.fullmatch(invoice):
            pair_errors.append("Invalid invoice format")
        if po and not PO_PATTERN.fullmatch(po):
            pair_errors.append("PO must be exactly 10 digits")
        if source is not None and source not in SOURCES:
            pair_errors.append("Invalid identifier source")
        if pair_errors:
            errors.extend(pair_errors)
        validated_pairs.append({
            "invoice_number": invoice or None,
            "po_number": po or None,
            "source": source,
            "valid": not pair_errors,
            "errors": pair_errors,
        })

    status = "VALIDATION_FAILED" if errors else "VALIDATED"
    return {
        **event,  # Preserve SF2 context for downstream stages.
        "case_id": case_id,
        "message_id": message_id,
        "invoice_ids": valid,
        "invalid_invoice_ids": invalid,
        "invoice_count": len(valid),
        "validated_identifier_pairs": validated_pairs,
        "validation_status": status,
        "validation_errors": errors,
        "next_stage": "INVOICE_SCOPE_DETERMINATION" if status == "VALIDATED" else "MANUAL_REVIEW",
    }


def lambda_handler(event, context):
    logger.info("Identifier validation invoked")
    try:
        result = validate_identifiers(event)
        logger.info("Identifier validation completed: status=%s count=%s",
                    result["validation_status"], result["invoice_count"])
        return result
    except (ValueError, TypeError) as exc:
        logger.warning("Invalid identifier validation input: %s", exc)
        return {
            **(event if isinstance(event, dict) else {}),
            "validation_status": "VALIDATION_FAILED",
            "validation_errors": [str(exc)],
            "invoice_ids": [],
            "invoice_count": 0,
            "next_stage": "MANUAL_REVIEW",
        }
