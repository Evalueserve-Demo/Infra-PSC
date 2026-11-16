"""PSC #230 Mail Preprocessing Lambda.

Cleans the mail with the same rules as payment_query_platform.text and returns
the fields SF2 expects.

No SQL is executed here: #190 persists case/message/attachment records.
This Lambda prepares bounded email-only inputs for both intake agents.
Attachment text is incorporated later only after its guardrail screening.
"""

import logging
import os
import boto3
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Same as payment_query_platform/text.py.
TAGS = re.compile(r"<[^>]+>")
SCRIPT_AND_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
WHITESPACE = re.compile(r"\s+")
MAX_BODY_CHARS = 20000


def plain_text(body: str | None, limit: int = MAX_BODY_CHARS) -> str:
    """Remove scripts, styles and tags, and cap the length."""
    if not body:
        return ""
    text = SCRIPT_AND_STYLE.sub(" ", body)
    text = WHITESPACE.sub(" ", TAGS.sub(" ", text)).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " [truncated]"


def required_string(data: Dict[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def lambda_handler(event, context):
    """Return SF2's required PREPROCESSED result without changing case IDs.

    SF2 expects case_id, message_id, subject, preprocessed_body,
    attachments, and processing_status. No DB writes or direct AgentCore
    calls belong in this preprocessing stage. Bedrock ApplyGuardrail is used.
    """
    if not isinstance(event, dict):
        raise ValueError("Expected JSON object")

    case_id = required_string(event, "case_id")
    message_id = required_string(event, "message_id")
    body = event.get("body") or ""
    subject = event.get("subject") or ""
    attachments = event.get("attachments", [])

    if not isinstance(body, str):
        raise ValueError("body must be a string")
    if not isinstance(subject, str):
        raise ValueError("subject must be a string")
    if not isinstance(attachments, list):
        raise ValueError("attachments must be a list")

    # Attachment content is not read here, so the malware scan and the
    # guardrail still apply to it later.
    normalized_body = plain_text(body)
    normalized_subject = WHITESPACE.sub(" ", TAGS.sub(" ", subject)).strip()

    # Same layout as intake/inputs.py, for the email only. Attachment text is in S3
    # and must pass the guardrail before an agent sees it, so it is not added here.
    sender = event.get("from") or ""
    if isinstance(sender, dict):
        sender = ((sender.get("emailAddress") or {}).get("address") or "")
    if not isinstance(sender, str):
        sender = ""
    classification_text = classification_input(normalized_subject, normalized_body, sender)
    extraction_text = extraction_input(normalized_subject, normalized_body)

    # Screen the email BEFORE downstream AgentCore invocation.
    screening = screen(normalized_subject, normalized_body)
    guardrail_status = "BLOCKED" if screening.blocked else "ALLOWED"
    screened_attachments = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise ValueError("each attachment must be an object")
        item = dict(attachment)
        extracted = item.get("extracted_text")
        if isinstance(extracted, str) and extracted:
            result = screen_attachment(_attachment_label(item), extracted)
            if result.withheld:
                item.pop("extracted_text", None)
                item["guardrail_status"] = "WITHHELD"
                item["guardrail_reason"] = result.reason
            else:
                item["extracted_text"] = result.text
                item["guardrail_status"] = "ALLOWED"
        screened_attachments.append(item)

    # Only screened text may be sent to the extractor.
    if not screening.blocked:
        extraction_text = extraction_input(normalized_subject, normalized_body, screened_attachments)
    else:
        classification_text = ""
        extraction_text = ""

    logger.info("Mail preprocessing/guardrail completed for case %s: %s", case_id, guardrail_status)
    return {
        "case_id": case_id,
        "message_id": message_id,
        "conversation_id": event.get("conversation_id"),
        "from": event.get("from"),
        "to": event.get("to"),
        "subject": normalized_subject,
        "preprocessed_body": normalized_body,
        "classification_input": classification_text,
        "extraction_input": extraction_text,
        "guardrail_status": guardrail_status,
        "guardrail_reason": screening.reason,
        "attachments": screened_attachments,
        "processing_status": "BLOCKED" if screening.blocked else "PREPROCESSED",
    }


# The agent input layout, as in intake/inputs.py.
def _attachment_label(attachment: dict) -> str:
    return attachment.get("filename") or attachment.get("name") or "attachment"


def classification_input(subject: str, body: str, sender: str = "",
                         attachments: Optional[List[dict]] = None) -> str:
    """The email for the classifier, with a short excerpt of each attachment.

    The classifier only decides whether the email is ours, so it sees enough of
    an attachment to recognise an invoice, not the whole document.
    """
    text = f"From: {sender}\nSubject: {subject}\n\n{body}"
    if attachments:
        lines = ["", "", "ATTACHMENTS:"]
        for attachment in attachments:
            excerpt = attachment.get("excerpt")
            lines.append(f"- {_attachment_label(attachment)}" + (f":\n{excerpt}" if excerpt else ""))
        text += "\n".join(lines)
    return text


def extraction_input(subject: str, body: str, attachments: Optional[List[dict]] = None) -> str:
    """The email and the full text of each attachment, for the extractor."""
    parts = [f"EMAIL SUBJECT:\n{subject}", f"\nEMAIL BODY:\n{body}"]
    for attachment in attachments or []:
        text = attachment.get("extracted_text")
        if not text:
            continue
        parts.append(f"\nATTACHMENT ({_attachment_label(attachment)}):\n{text}")
    return "\n".join(parts)


# --- Guardrail screening, as in intake/guardrail.py ---
class GuardrailResult:
    """What the screen decided about one email."""

    def __init__(self, blocked: bool, reason: Optional[str] = None, assessment: Any = None):
        self.blocked = blocked
        self.reason = reason
        self.assessment = assessment

    def as_dict(self) -> Dict[str, Any]:
        return {"blocked": self.blocked, "reason": self.reason}


def withheld(reason: str) -> dict:
    """The classification for an email that was stopped before it could be classified."""
    return {"is_payment_related": False, "request_type": "BLOCKED_BY_GUARDRAIL", "confidence": 10,
            "query_summary": f"Withheld: {reason}."}


def screen(subject: str, body: str) -> GuardrailResult:
    """Run the email content through the guardrail.

    Returns blocked=True when no guardrail is configured (fail closed). A guardrail that
    cannot be reached is treated as a block, because failing open would let the
    thing it guards against straight through.
    """
    guardrail_id = os.getenv("GUARDRAIL_ID")
    if not guardrail_id:
        logger.error("Guardrail configuration missing")
        return GuardrailResult(blocked=True, reason="Guardrail configuration missing")

    client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))
    content = f"Subject: {subject}\n\n{body}"

    try:
        response = client.apply_guardrail(
            guardrailIdentifier=guardrail_id,
            guardrailVersion=os.getenv("GUARDRAIL_VERSION", "DRAFT"),
            source="INPUT",
            content=[{"text": {"text": content}}],
        )
    except Exception as exc:
        # Fail closed: an email that could not be screened does not get to the
        # model. Better a false quarantine than an unscreened injection.
        logger.error("Guardrail could not be applied: %s", exc)
        return GuardrailResult(blocked=True, reason=f"Guardrail unavailable: {type(exc).__name__}")

    action = response.get("action")
    if action == "GUARDRAIL_INTERVENED":
        reason = _summarise(response.get("assessments") or [])
        logger.warning("Guardrail intervened: %s", reason)
        return GuardrailResult(blocked=True, reason=reason, assessment=response.get("assessments"))

    return GuardrailResult(blocked=False, assessment=response.get("assessments"))


class AttachmentScreen:
    """What the screen decided about one attachment's text.

    text is what an agent may read: the original, the guardrail's masked
    version when it only anonymised something, or None when it is withheld.
    """

    def __init__(self, text: Optional[str], reason: Optional[str] = None):
        self.text = text
        self.reason = reason

    @property
    def withheld(self) -> bool:
        return self.text is None


def _blocks(assessments: list) -> bool:
    """Did any policy block, rather than only anonymise?"""
    for assessment in assessments:
        policies = [
            (assessment.get("contentPolicy") or {}).get("filters", []),
            (assessment.get("topicPolicy") or {}).get("topics", []),
            (assessment.get("wordPolicy") or {}).get("customWords", []),
            (assessment.get("sensitiveInformationPolicy") or {}).get("piiEntities", []),
            (assessment.get("sensitiveInformationPolicy") or {}).get("regexes", []),
        ]
        if any(item.get("action") == "BLOCKED" for policy in policies for item in policy):
            return True
    return False


def screen_attachment(name: str, text: str) -> AttachmentScreen:
    """Run one attachment's text through the guardrail.

    Like screen(), it withholds when guardrail configuration is missing or
    the guardrail cannot be reached. Unlike screen(), a masked
    entity is not a block: an invoice routinely carries bank details, and the
    guardrail's masked text is used instead of dropping the file.
    """
    guardrail_id = os.getenv("GUARDRAIL_ID")
    if not guardrail_id:
        return AttachmentScreen(None, "Guardrail configuration missing")

    client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))
    header = f"Attachment: {name}\n\n"
    try:
        response = client.apply_guardrail(
            guardrailIdentifier=guardrail_id,
            guardrailVersion=os.getenv("GUARDRAIL_VERSION", "DRAFT"),
            source="INPUT",
            content=[{"text": {"text": header + text}}],
        )
    except Exception as exc:
        logger.error("Guardrail could not be applied to %s: %s", name, exc)
        return AttachmentScreen(None, f"Guardrail unavailable: {type(exc).__name__}")

    if response.get("action") != "GUARDRAIL_INTERVENED":
        return AttachmentScreen(text)

    assessments = response.get("assessments") or []
    if _blocks(assessments):
        reason = _summarise(assessments)
        logger.warning("Guardrail withheld attachment %s: %s", name, reason)
        return AttachmentScreen(None, reason)

    masked = "".join(output.get("text", "") for output in response.get("outputs") or [])
    masked = masked[len(header):] if masked.startswith(header) else masked
    return AttachmentScreen(masked or None, "sensitive details masked")


def _summarise(assessments: list) -> str:
    """A short, readable reason from the guardrail's assessment."""
    reasons = []
    for assessment in assessments:
        if assessment.get("topicPolicy"):
            reasons += [t["name"] for t in assessment["topicPolicy"].get("topics", [])
                        if t.get("action") == "BLOCKED"]
        if assessment.get("contentPolicy"):
            reasons += [f"{f['type']} content" for f in assessment["contentPolicy"].get("filters", [])
                        if f.get("action") == "BLOCKED"]
        if assessment.get("sensitiveInformationPolicy"):
            reasons += ["PII detected"]
        # Prompt-attack detection lives under the content policy with type
        # PROMPT_ATTACK; surfaced plainly because it is the main inbound risk.
    return ", ".join(dict.fromkeys(reasons)) or "blocked by guardrail policy"
