"""PSC #190 Email Processing.
Finds or opens the case for the mail, saves the message and its attachments,
and hands the case to SQS1. Used by SF1.
Needs psycopg[binary] in the package and boto3 from the Lambda runtime.
IAM-authenticated RDS Proxy requires AURORA_HOST, AURORA_DB, AURORA_USER.
"""
import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
from uuid import UUID
import boto3
import psycopg
from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
STATUS_RECEIVED = "RECEIVED"
STATUS_READY = "READY_FOR_DOWNSTREAM"
CASE_STATUS_OPEN = "OPEN"
CASE_ID_FORMAT = "CASE-%06d"
TAGS = re.compile(r"<[^>]+>")
SCRIPT_AND_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
WHITESPACE = re.compile(r"\s+")
MAX_BODY_CHARS = 20000

def plain_text(body: str | None, limit: int = MAX_BODY_CHARS) -> str:
    """Strip HTML to readable text and cap its length."""
    if not body:
        return ""
    text = SCRIPT_AND_STYLE.sub(" ", body)
    text = WHITESPACE.sub(" ", TAGS.sub(" ", text)).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " [truncated]"

class Database:
    """One IAM-authenticated connection, reused across warm invocations."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: int = 5432,
        dbname: Optional[str] = None,
        user: Optional[str] = None,
        region: Optional[str] = None,
    ):
        self.host = host or os.environ["AURORA_HOST"]
        self.port = port
        self.dbname = dbname or os.getenv("AURORA_DB", "appdb")
        self.user = user or os.getenv("AURORA_USER", "integ_app")
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self._connection: Optional[psycopg.Connection] = None

    def _auth_token(self) -> str:
        return boto3.client("rds", region_name=self.region).generate_db_auth_token(
            DBHostname=self.host, Port=self.port, DBUsername=self.user, Region=self.region
        )

    def connection(self) -> psycopg.Connection:
        if self._connection is not None and not self._connection.closed:
            return self._connection
        self._connection = psycopg.connect(
            host=self.host,
            port=self.port,
            dbname=self.dbname,
            user=self.user,
            password=self._auth_token(),
            sslmode="require",
            connect_timeout=15,
            autocommit=True,
        )
        return self._connection

    def cursor(self):
        return self.connection().cursor()

    def close(self) -> None:
        if self._connection is not None and not self._connection.closed:
            self._connection.close()

class MessageRepository:
    """One row per Graph message."""

    INSERT = """
    insert into ps.messages
           (message_id, internet_message_id, in_reply_to_message_id,
            conversation_id, sender, recipient, subject, body, body_hash,
            has_attachment, status, received_at)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    on conflict (message_id) do nothing
    returning message_id
    """

    SELECT = "select status from ps.messages where message_id = %s"

    STAGED_MESSAGE = """
        select internet_message_id, in_reply_to_message_id, conversation_id,
            sender, recipient, subject, body, body_hash, has_attachment, received_at
    from ps.messages
    where message_id = %s
    """

    def __init__(self, database: Database):
        self._database = database

    def save_if_absent(self, message_id: str, detail: Dict[str, Any], event_time: Optional[str]) -> bool:
        """Insert one message as RECEIVED. False when it was already stored."""
        with self._database.cursor() as cursor:
            cursor.execute(
                self.INSERT,
                (
                    message_id,
                    detail.get("internet_message_id"),
                    detail.get("in_reply_to_message_id"),
                    detail.get("conversation_id"),
                    detail.get("sender") or "",
                    detail.get("recipient") or "",
                    detail.get("subject"),
                    detail.get("body"),
                    detail.get("body_hash"),
                    bool(detail.get("has_attachment")),
                    STATUS_RECEIVED,
                    event_time,
                ),
            )
            return cursor.fetchone() is not None

    def get(self, message_id: str) -> Optional[Dict[str, Any]]:
        with self._database.cursor() as cursor:
            cursor.execute(self.SELECT, (message_id,))
            row = cursor.fetchone()
            return {"status": row[0]} if row else None

    def get_staged_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        with self._database.cursor() as cursor:
            cursor.execute(self.STAGED_MESSAGE, (message_id,))
            row = cursor.fetchone()
            if row is None:
                return None

            raw_body = row[6] or ""
            body = plain_text(raw_body, limit=len(raw_body) + 1)

            if len(row) >= 10:
                body_hash = row[7] or hashlib.sha256(body.encode("utf-8")).hexdigest()
                has_attachment = row[8]
                received_at = row[9]
            else:
                body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
                has_attachment = row[7]
                received_at = row[8]

            return {
                "message_id": message_id,
                "internet_message_id": row[0],
                "in_reply_to_message_id": row[1],
                "conversation_id": row[2],
                "sender": row[3],
                "recipient": row[4],
                "subject": row[5],
                "body": body,
                "body_hash": body_hash,
                "has_attachment": has_attachment,
                "received_at": received_at.isoformat() if hasattr(received_at, "isoformat") else received_at,
            }

    def mark_ready(self, message_id: str) -> None:
        with self._database.cursor() as cursor:
            cursor.execute(
                "update ps.messages set status = %s, processed_at = now() where message_id = %s",
                (STATUS_READY, message_id),
            )

class CaseRepository:
    """Persist case records in the case schema."""

    FIND = "select case_id from ps.case_header where conversation_id = %s"

    INSERT = """
    insert into ps.case_header (conversation_id)
    values (%s)
    on conflict (conversation_id) do nothing
    returning case_id
    """

    INSERT_MESSAGE = """
    insert into ps.case_message
        (case_id, direction, internet_message_id, graph_message_id,
         in_reply_to_message_id, sender_address, recipient_addresses, subject,
         body_text, body_hash, received_at)
    values (%s, 'INBOUND', %s, %s, %s, %s, %s, %s, %s, %s, %s)
    on conflict (internet_message_id) do nothing
    returning message_id
    """

    FIND_MESSAGE = """
    select message_id, case_id from ps.case_message where internet_message_id = %s
    """

    ADD_ATTACHMENT = """
    insert into ps.case_attachment
        (case_id, message_id, file_name, mime_type, size_bytes, sha256, storage_uri,
         extraction_status, extracted_text_uri)
    values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    on conflict (case_id, sha256) do nothing
    returning attachment_id
    """

    ATTACHMENTS_OF_MESSAGE = """
    select a.file_name, a.mime_type, a.size_bytes, a.sha256, a.storage_uri,
           a.extraction_status, a.extracted_text_uri
    from ps.case_attachment a join ps.case_message m using (message_id)
    where m.graph_message_id = %s
    order by a.created_at
    """

    def __init__(self, database: Database):
        self._database = database

    def find_by_conversation(self, conversation_id: str) -> Optional[str]:
        with self._database.cursor() as cursor:
            cursor.execute(self.FIND, (conversation_id,))
            row = cursor.fetchone()
            return str(row[0]) if row else None

    def open_case(self, conversation_id: str) -> str:
        """Create or reuse the UUID case for one conversation."""
        for _ in range(10):
            with self._database.cursor() as cursor:
                cursor.execute(self.INSERT, (conversation_id,))
                row = cursor.fetchone()
                if row is not None:
                    return str(row[0])
            existing = self.find_by_conversation(conversation_id)
            if existing:
                return existing
        raise RuntimeError("Could not allocate a case ID after repeated contention")

    def mark_out_of_scope(self, case_id: str) -> None:
        """The desk has nothing to answer, so the case is finished. One already further along keeps its status."""
        with self._database.cursor() as cursor:
            cursor.execute("update ps.case_header set case_status = 'OUT_OF_SCOPE' "
                           "where case_id = %s and case_status = 'NEW'", (UUID(case_id),))

    def add_message(self, case_id: str, message: Dict[str, Any]) -> str:
        with self._database.cursor() as cursor:
            cursor.execute(
                self.INSERT_MESSAGE,
                (
                    UUID(case_id),
                    message["internet_message_id"],
                    message.get("message_id"),
                    message.get("in_reply_to_message_id"),
                    message.get("sender") or "",
                    message.get("recipient") or "",
                    message.get("subject"),
                    message.get("body"),
                    message["body_hash"],
                    message["received_at"],
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return str(row[0])

            cursor.execute(self.FIND_MESSAGE, (message["internet_message_id"],))
            existing = cursor.fetchone()
            if existing is None or str(existing[1]) != case_id:
                raise RuntimeError("Internet message ID already belongs to another payment case")
            return str(existing[0])

    def add_attachments(
        self, case_id: str, message_id: str, attachments: List[Dict[str, Any]]
    ) -> int:
        """Record where each attachment and its text were stored. Both are in S3.

        An attachment stored before text extraction existed carries no status,
        and is recorded as PENDING, the column's own default.
        """
        stored = 0
        with self._database.cursor() as cursor:
            for attachment in attachments:
                if not attachment.get("s3_reference"):
                    continue
                size_bytes = attachment.get("size_bytes")
                if not size_bytes or size_bytes < 1:
                    raise ValueError("Attachments must have a positive size")
                cursor.execute(
                    self.ADD_ATTACHMENT,
                    (
                        UUID(case_id),
                        UUID(message_id),
                        attachment.get("name") or "unnamed",
                        attachment.get("content_type") or "application/octet-stream",
                        size_bytes,
                        attachment["sha256"],
                        attachment["s3_reference"],
                        attachment.get("extraction_status") or "PENDING",
                        attachment.get("extracted_text_uri"),
                    ),
                )
                stored += cursor.fetchone() is not None
        return stored

    def attachments_of(self, graph_message_id: str) -> List[Dict[str, Any]]:
        """The attachments recorded for one message, as Stage 2 hands them on.

        A file the thread already had is recorded once, under the message that
        first brought it, so it is not listed again for a later one.
        """
        with self._database.cursor() as cursor:
            cursor.execute(self.ATTACHMENTS_OF_MESSAGE, (graph_message_id,))
            return [
                {"name": name, "content_type": mime, "size_bytes": size, "sha256": sha256,
                 "s3_reference": uri, "extraction_status": status, "extracted_text_uri": text_uri}
                for name, mime, size, sha256, uri, status, text_uri in cursor.fetchall()
            ]

# The attachment references the next stage reads.
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

ATTACHMENT_REFERENCE_FIELDS = (
    "name", "content_type", "size_bytes", "sha256", "s3_reference",
    "extraction_status", "extracted_text_uri",
)
_database: Optional[Database] = None

def _get_database() -> Database:
    global _database
    if _database is None:
        _database = Database()
    return _database


def _unwrap_sf1(event: Dict[str, Any]) -> Dict[str, Any]:
    """Accept what SF1 passes on from #180 (message_id and its attachments).

    A direct event that carries the whole message still works.
    """
    if not isinstance(event, dict):
        raise ValueError("SF1 input must be a JSON object")
    payload = event.get("attachment_result")
    if payload is None:
        return dict(event)
    if not isinstance(payload, dict):
        raise ValueError("attachment_result must be an object")
    payload = payload.get("Payload", payload)
    if not isinstance(payload, dict):
        raise ValueError("attachment_result.Payload must be an object")
    merged = dict(event)
    for key in ("message_id", "conversation_id", "sender", "recipient", "subject",
                "body", "has_attachment", "attachment_id", "attachment_s3_reference"):
        if key not in merged or merged[key] is None:
            if key in payload:
                merged[key] = payload[key]
    if "attachments" in payload:
        merged["attachments"] = payload["attachments"]
    if merged.get("message_id") and payload.get("message_id") and merged["message_id"] != payload["message_id"]:
        raise ValueError("SF1 attachment result message_id mismatch")
    return merged


def _persist_scan_statuses(database: Database, case_id: str, attachments: List[Dict[str, Any]]) -> None:
    """Architecture adaptation: carry malware scanner result into DBA scan_status.

    Only explicit allowed statuses are written. No inference of CLEAN.
    """
    allowed = {"PENDING", "CLEAN", "THREAT", "FAILED", "UNSUPPORTED"}
    with database.cursor() as cur:
        for item in attachments:
            if not item.get("s3_reference"):
                continue
            status = item.get("scan_status") or "PENDING"
            if status not in allowed:
                raise ValueError(f"Unsupported attachment scan_status: {status}")
            if status != "CLEAN" and item.get("extraction_status") == "EXTRACTED":
                raise ValueError("Attachment cannot be EXTRACTED unless scan_status is CLEAN")
            if status != "PENDING":
                cur.execute(
                    "UPDATE ps.case_attachment SET scan_status = %s "
                    "WHERE case_id = %s AND sha256 = %s",
                    (status, UUID(case_id), item["sha256"]),
                )


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """Steps 17 to 20: look up the case, reuse or open it, save, hand over to SQS1."""
    event = _unwrap_sf1(event)
    message_id = event["message_id"]
    if not isinstance(message_id, str) or not message_id.strip():
        raise ValueError("message_id is required")
    database = _get_database()
    messages = MessageRepository(database)
    cases = CaseRepository(database)

    # Stage 1 is authoritative for message metadata; do not trust a sparse SF1 envelope.
    stored = messages.get(message_id)
    source_message = messages.get_staged_message(message_id)
    if source_message is None:
        raise ValueError(f"Message {message_id} is missing from ps.messages")
    if not source_message.get("internet_message_id"):
        raise ValueError(f"Message {message_id} has no internet message ID")
    conversation_id = source_message.get("conversation_id")
    if not conversation_id:
        raise ValueError(f"Message {message_id} has no conversation_id")
    logger.info("Message exists=%s status=%s conversation=%s",
                stored is not None, (stored or {}).get("status"), conversation_id)

    case_id = cases.find_by_conversation(conversation_id)
    if not case_id:
        case_id = cases.open_case(conversation_id)
        logger.info("Opened case %s for conversation %s", case_id, conversation_id)

    # Start from the message as stored in the database, then add what the event carries.
    normalized_event = {**source_message, **event,
                        "message_id": message_id,
                        "conversation_id": conversation_id,
                        "sender": source_message.get("sender"),
                        "recipient": source_message.get("recipient"),
                        "subject": source_message.get("subject"),
                        "body": source_message.get("body"),
                        "has_attachment": source_message.get("has_attachment")}
    attachments = normalized_event.get("attachments") or []
    if not isinstance(attachments, list):
        raise ValueError("attachments must be a list")
    normalized = normalized_context(normalized_event, case_id)
    case_message_id = cases.add_message(case_id, source_message)
    stored_attachments = cases.add_attachments(case_id, case_message_id, attachments)
    _persist_scan_statuses(database, case_id, attachments)
    logger.info("Case %s linked message %s and %d new attachments",
                case_id, message_id, stored_attachments)

    # Step 20: hand the case to SQS1. Nothing is classified here.
    queue_url = os.environ["SQS1_QUEUE_URL"]
    body = json.dumps(normalized, ensure_ascii=False, default=str)
    if len(body.encode("utf-8")) > 256 * 1024:
        raise ValueError("Normalized email exceeds SQS maximum message size")
    boto3.client("sqs").send_message(QueueUrl=queue_url, MessageBody=body)
    messages.mark_ready(message_id)
    logger.info("Sent case %s to SQS1", case_id)
    return normalized
