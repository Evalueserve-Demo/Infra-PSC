"""STAGE 2, part two — the Email Processing Consumer.

Invoked by Step Function 1 after any attachment has been stored, and follows
the flow specification:

    17. Read existing state: does the message exist, what is its status, does
        the conversation already belong to a case?
    18. Prepare the normalized processing context, opening a new case when the
        conversation does not have one
    19. Save the case, conversation, message and attachment relationship
    20. Send the normalized message to SQS1

This stage deliberately does no business work. It does not validate the
invoice, classify the mail, call S/4 or Ariba, or read the PDF. It establishes
identity and context, and hands off.

Environment:
    AURORA_HOST          cluster endpoint
    AURORA_DB            database name, defaults to appdb
    AURORA_USER          database user, defaults to integ_app
    SQS1_QUEUE_URL       the classification queue
"""

import json
import logging
import os
from typing import Any, Dict, List

import boto3

from database import CaseRepository, Database, MessageRepository

logger = logging.getLogger()
logger.setLevel(logging.INFO)

STATE_READY = "READY_FOR_DOWNSTREAM"

_database: Database | None = None


def _get_database() -> Database:
    global _database
    if _database is None:
        _database = Database()
    return _database


ATTACHMENT_REFERENCE_FIELDS = (
    "name", "content_type", "size_bytes", "sha256", "s3_reference",
    "extraction_status", "extracted_text_uri",
)


def attachment_references(attachments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every stored attachment, as references only.

    The text stays in S3 and is read by the stage that needs it: carrying it
    here would push the message past the 256 KB SQS and Step Functions limit.
    """
    return [
        {field: attachment.get(field) for field in ATTACHMENT_REFERENCE_FIELDS}
        for attachment in attachments
        if attachment.get("s3_reference")
    ]


def normalized_context(event: Dict[str, Any], case_id: str) -> Dict[str, Any]:
    """Step 18 — the contract every downstream stage reads."""
    return {
        "case_id": case_id,
        "message_id": event["message_id"],
        "conversation_id": event.get("conversation_id"),
        "sender": event.get("sender"),
        "recipient": event.get("recipient"),
        "subject": event.get("subject"),
        "body": event.get("body"),
        "has_attachment": bool(event.get("has_attachment")),
        "attachment_id": event.get("attachment_id"),
        "attachment_s3_reference": event.get("attachment_s3_reference"),
        "attachments": attachment_references(event.get("attachments") or []),
    }


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    message_id = event["message_id"]
    conversation_id = event.get("conversation_id")
    if not conversation_id:
        raise ValueError(f"Message {message_id} has no conversation_id; cannot establish a case")

    database = _get_database()
    messages = MessageRepository(database)
    cases = CaseRepository(database)

    # Step 17 — what does Aurora already know?
    stored = messages.get(message_id)
    source_message = messages.get_staged_message(message_id)
    if source_message is None:
        raise ValueError(f"Message {message_id} is missing from integration staging")
    if not source_message.get("internet_message_id"):
        raise ValueError(
            f"Message {message_id} has no internet message ID; backfill staging before PS cutover"
        )
    existing_case = cases.find_by_conversation(conversation_id)
    logger.info(
        "Message exists=%s status=%s conversation=%s existing case=%s",
        stored is not None,
        (stored or {}).get("status"),
        conversation_id,
        existing_case,
    )

    # Step 18 — reuse the thread's case, or open one.
    if existing_case:
        case_id = existing_case
    else:
        case_id = cases.open_case(conversation_id)
        logger.info("Opened %s for conversation %s", case_id, conversation_id)

    normalized = normalized_context(event, case_id)

    # Step 19 — persist the relationship, then mark the message handed on.
    # Attaching the message to the case is what links the two; the foreign key
    # means the attachments below cannot be recorded against a case or message
    # that does not exist.
    case_message_id = cases.add_message(case_id, source_message)
    stored_attachments = cases.add_attachments(
        case_id, case_message_id, event.get("attachments") or []
    )
    messages.mark_ready(message_id)
    logger.info("Case %s now holds this message and %d attachment(s)", case_id, stored_attachments)

    # Step 20 — hand the normalized context to SQS1.
    queue_url = os.environ["SQS1_QUEUE_URL"]
    boto3.client("sqs").send_message(QueueUrl=queue_url, MessageBody=json.dumps(normalized))
    logger.info("Sent %s to SQS1", case_id)

    return normalized
