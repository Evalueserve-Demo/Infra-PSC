"""Aurora persistence for the ingestion and processing stages.

Connects with IAM database authentication, so no password is stored anywhere.
The Lambda role needs rds-db:connect, and the integ_app database user must have
been granted rds_iam.

Two pipeline tables in the ps schema, created by infra/schema_ps.sql beside the
case tables:

    graph_sync_state   where the mailbox has been read up to
    messages           one row per Graph message, with how far it got

The unique constraint on ps.case_header.conversation_id is what guarantees a
thread can only ever have one case.
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

from text import plain_text

logger = logging.getLogger(__name__)

# Message lifecycle, matching the flow specification.
STATUS_RECEIVED = "RECEIVED"
STATUS_READY = "READY_FOR_DOWNSTREAM"

CASE_STATUS_OPEN = "OPEN"
CASE_ID_FORMAT = "CASE-%06d"


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


class SyncStateRepository:
    """Holds the committed Graph delta link, one row per mailbox.

    The link is only ever written after the messages it covers are persisted,
    which is what makes a crash mid-run safe: the next run re-reads from the
    last committed point and the message insert deduplicates the overlap.
    """

    SELECT = "select delta_link from ps.graph_sync_state where mailbox = %s"

    UPSERT = """
    insert into ps.graph_sync_state (mailbox, delta_link)
    values (%s, %s)
    on conflict (mailbox) do update
        set delta_link = excluded.delta_link, committed_at = now()
    """

    def __init__(self, database: Database, mailbox: str):
        self._database = database
        self._mailbox = mailbox

    def get(self) -> Optional[str]:
        with self._database.cursor() as cursor:
            cursor.execute(self.SELECT, (self._mailbox,))
            row = cursor.fetchone()
            return row[0] if row else None

    def commit(self, delta_link: str) -> None:
        with self._database.cursor() as cursor:
            cursor.execute(self.UPSERT, (self._mailbox, delta_link))


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


class IdentifierRepository:
    """Persist identifiers extracted from a case message."""

    INSERT = """
    insert into ps.case_identifier
        (case_id, identifier_type, supplied_value, canonical_value, source, validation_status)
    values (%s, %s, %s, %s, %s, %s)
    on conflict do nothing
    returning identifier_id
    """

    SOURCE_MAP = {
        "EMAIL_SUBJECT": "SUBJECT",
        "EMAIL_BODY": "EMAIL_BODY",
        "ATTACHMENT": "ATTACHMENT",
    }

    def __init__(self, database: Database):
        self._database = database

    def save_candidates(self, case_id: str, candidates: List[Dict[str, Any]]) -> int:
        stored = 0
        with self._database.cursor() as cursor:
            for candidate in candidates:
                source = self.SOURCE_MAP.get(candidate.get("found_in"))
                if source is None:
                    raise ValueError("Extracted identifiers require a recognized found_in source")
                for identifier_type, field in (("INVOICE", "invoice_number"), ("PO", "po_number")):
                    supplied = candidate.get(field)
                    if not supplied:
                        continue
                    supplied = str(supplied).strip()
                    if not supplied or len(supplied) > 100:
                        raise ValueError(f"{identifier_type} identifier must contain 1 to 100 characters")
                    canonical = supplied if re.fullmatch(r"[A-Za-z0-9_-]{1,16}", supplied) else None
                    validation_status = "PENDING"
                    if identifier_type == "INVOICE":
                        validation_status = (
                            "VALID" if re.fullmatch(r"INV-\d{4,10}", supplied) else "INVALID_FORMAT"
                        )
                    cursor.execute(
                        self.INSERT,
                        (UUID(case_id), identifier_type, supplied, canonical, source, validation_status),
                    )
                    stored += cursor.fetchone() is not None
        return stored


class AgentCallLogRepository:
    """Append an auditable record for each model-runtime invocation."""

    INSERT = """
    insert into ps.integration_call_log
        (correlation_id, case_id, component, invoked_by, target_system, operation,
         outcome, latency_ms, request_hash, prompt_hash, request_payload,
         response_payload, model_id, error_code, error_message)
    select c.correlation_id, c.case_id, %s, %s, 'BEDROCK', %s,
           %s, %s, %s, %s, %s, %s, %s, %s, %s
    from ps.case_header c
    where c.case_id = %s
    returning call_id
    """

    def __init__(self, database: Database):
        self._database = database

    def record(
        self,
        case_id: str,
        component: str,
        invoked_by: str,
        operation: str,
        model_id: str,
        system_prompt: str,
        user_input: Any,
        structured_output_instruction: str,
        output_schema: Any,
        outcome: str,
        latency_ms: int,
        response_payload: Optional[Dict[str, Any]] = None,
        error: Optional[Exception] = None,
    ) -> int:
        request_payload: Dict[str, Any] = {
            "system_prompt": system_prompt,
            "user_input": user_input,
            "structured_output_instruction": structured_output_instruction,
            "output_schema": output_schema,
        }
        encoded_request = json.dumps(request_payload, sort_keys=True, separators=(",", ":"))
        prompt_material = "\n".join(
            (
                system_prompt,
                structured_output_instruction,
                json.dumps(output_schema, sort_keys=True, separators=(",", ":")),
            )
        )
        prompt_hash = hashlib.sha256(prompt_material.encode("utf-8")).hexdigest()
        request_hash = hashlib.sha256(encoded_request.encode("utf-8")).hexdigest()
        error_code = type(error).__name__ if error else None
        error_message = str(error)[:1000] if error else None

        with self._database.cursor() as cursor:
            cursor.execute(
                self.INSERT,
                (
                    component,
                    invoked_by,
                    operation,
                    outcome,
                    latency_ms,
                    request_hash,
                    prompt_hash,
                    Jsonb(request_payload),
                    Jsonb(response_payload) if response_payload is not None else None,
                    model_id,
                    error_code,
                    error_message,
                    UUID(case_id),
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"Cannot audit agent call for unknown case {case_id}")
            return row[0]

    def try_record(self, **call) -> None:
        """record(), but a failure is logged and never raised: the audit must not fail the call it describes."""
        try:
            self.record(**call)
        except Exception:
            logger.exception("Could not record the agent call for case %s", call.get("case_id"))
