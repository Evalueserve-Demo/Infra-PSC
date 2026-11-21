"""STAGE 1 — Email ingestion.

Invoked on a schedule by EventBridge, and follows the flow specification step
for step:

    2. Read the previous Graph sync state from Aurora
    3. Get the Graph credentials from Secrets Manager
    4. Call Microsoft Graph
    5. Receive the changed messages and the next sync state
    6. Idempotency check, then persist each message as RECEIVED
    7. Confirm the messages are safely persisted
    8. Commit the new sync state
    9. Publish each newly received message to pre-SF1 SQS (EventBridge Pipe starts SF1)

The ordering of 7 and 8 matters. The sync state advances only after the
messages it covers are in the database, so a crash anywhere in the run leaves
the state pointing at the last safe position. Re-reading that overlap is
harmless because the insert deduplicates on the Graph message ID.

Environment:
    MAIL_SOURCE          graph (default) reads Outlook; s3 reads CSV files from S3 instead
    MAIL_S3_BUCKET       with s3: the bucket holding the CSV files
    MAIL_S3_PREFIX       with s3: the folder in it, default inbox/
    GRAPH_API_BASE_URL   Graph base, e.g. https://graph.microsoft.com/v1.0
    GRAPH_SECRET_ID      Secrets Manager id holding the Azure AD credentials
    GRAPH_MAILBOX        mailbox to read; falls back to the secret's value
    AURORA_HOST          cluster endpoint
    AURORA_DB            database name, defaults to appdb
    AURORA_USER          database user, defaults to integ_app
    SQS_QUEUE_URL       Pre-SF1 SQS queue URL
"""

import json
import logging
import os
import time
from typing import Any, Dict, List

import boto3

from database import Database, MessageRepository, SyncStateRepository
from graph_client import (
    GraphApiError,
    GraphApiRepository,
    normalize,
    thread_context,
)
from graph_auth import GraphAuthError, GraphTokenProvider, load_credentials
from s3_mail import S3MailRepository, uses_s3
from text import plain_text

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Reused across warm invocations.
_database: Database | None = None


def _get_database() -> Database:
    global _database
    if _database is None:
        _database = Database()
    return _database


def build_mailbox(base_url: str = "") -> GraphApiRepository | S3MailRepository:
    """Step 3 — credentials from Secrets Manager, never from the environment."""
    if uses_s3():
        return S3MailRepository.from_env()
    credentials = load_credentials(os.environ["GRAPH_SECRET_ID"])
    mailbox = os.getenv("GRAPH_MAILBOX") or credentials.get("mailbox")
    if not mailbox:
        raise GraphAuthError("No mailbox configured; set GRAPH_MAILBOX or add it to the secret")
    return GraphApiRepository(base_url, mailbox=mailbox, token_provider=GraphTokenProvider(credentials))


def workflow_input(detail: Dict[str, Any]) -> Dict[str, Any]:
    """The case as the workflow receives it.

    The raw body does not travel: Outlook sends HTML, and both Step Functions
    and SQS cap a payload at 256 KB. The full body stays in Aurora; what goes
    downstream is the readable text, capped. Everything after this reads
    "body", so the key is kept and only its content changes.
    """
    return {**detail, "body": plain_text(detail.get("body"))}


def publish_to_sqs(queue_url: str, detail: Dict[str, Any]) -> None:
    """Publish a bounded message payload; Pipe, not this Lambda, starts SF1."""
    payload = workflow_input(detail)
    body = json.dumps(payload, default=str)
    if len(body.encode("utf-8")) > 256 * 1024:
        # Message body and metadata remain in Aurora. SF1 can load by message_id.
        body = json.dumps({"message_id": detail["message_id"]})
    args = {"QueueUrl": queue_url, "MessageBody": body}
    if queue_url.endswith(".fifo"):
        import hashlib
        args["MessageGroupId"] = "psc-email-ingestion"
        args["MessageDeduplicationId"] = hashlib.sha256(
            str(detail["message_id"]).encode("utf-8")
        ).hexdigest()
    boto3.client("sqs").send_message(**args)


def mark_enqueued(message_id: str) -> None:
    """Record successful SQS acceptance using columns in DBA schema_ps.sql."""
    with _get_database().cursor() as cursor:
        cursor.execute(
            "UPDATE ps.messages SET status = 'ENQUEUED', processed_at = now() "
            "WHERE message_id = %s AND status = 'RECEIVED' "
            "RETURNING message_id",
            (message_id,),
        )
        if cursor.fetchone() is None:
            raise RuntimeError(
                f"SQS accepted message {message_id}, but Aurora did not confirm its ENQUEUED status"
            )


def recover_unpublished(queue_url: str, context: Any = None, limit: int = 100) -> Dict[str, Any]:
    """Retry persisted RECEIVED messages not yet acknowledged by SQS.

    At-least-once only: a crash between SQS send and DB update can duplicate.
    Downstream SF1 must deduplicate by message_id.
    """
    repo = MessageRepository(_get_database())
    with _get_database().cursor() as cursor:
        cursor.execute(
            "SELECT message_id FROM ps.messages WHERE status = 'RECEIVED' "
            "AND ingested_at < now() - interval '2 minutes' "
            "ORDER BY ingested_at LIMIT %s",
            (limit,),
        )
        ids = [r[0] for r in cursor.fetchall()]
    succeeded, failed = 0, []
    for message_id in ids:
        if context and context.get_remaining_time_in_millis() < 30000:
            break
        try:
            detail = repo.get_staged_message(message_id)
            if detail is None:
                continue
            publish_to_sqs(queue_url, detail)
            mark_enqueued(message_id)
            succeeded += 1
        except Exception as exc:
            logger.exception("Recovery failed for %s", message_id)
            failed.append({"message_id": message_id, "error": str(exc)[:200]})
    return {"candidates": len(ids), "enqueued": succeeded, "failures": failed}


def publish_messages(queue_url: str, details: List[Dict[str, Any]], context: Any = None):
    # One mail every START_INTERVAL_SECONDS, so a burst does not start every
    # workflow in the same moment and hit the account's Lambda concurrency limit.
    interval = float(os.getenv("START_INTERVAL_SECONDS", "0"))
    published, failures = 0, []
    for detail in details:
        if context and context.get_remaining_time_in_millis() < 30_000:
            logger.warning("Nearly out of time; remaining persisted messages require reconciliation")
            break
        try:
            publish_to_sqs(queue_url, detail)
            mark_enqueued(detail["message_id"])
            published += 1
        except Exception as exc:
            logger.exception("Could not enqueue %s", detail["message_id"])
            failures.append({"message_id": detail["message_id"], "error": f"SQS: {exc}"})
        time.sleep(interval)
    return published, failures


def lambda_handler(event: Dict[str, Any] | None = None, context: Any = None) -> Dict[str, Any]:
    base_url = "" if uses_s3() else os.environ["GRAPH_API_BASE_URL"]
    workflow_enabled = os.getenv("ENABLE_WORKFLOW", "false").strip().lower() == "true"
    ingestion_enabled = os.getenv("ENABLE_INGESTION", "false").strip().lower() == "true"
    if not ingestion_enabled:
        return {"statusCode": 200, "body": {"status": "INGESTION_DISABLED"}}
    queue_url = os.environ.get("SQS_QUEUE_URL", "") if workflow_enabled else ""
    if (event or {}).get("action") == "RECOVER_UNPUBLISHED" and not workflow_enabled:
        return {"statusCode": 200, "body": {"status": "WORKFLOW_DISABLED", "messages_enqueued": 0}}
    if (event or {}).get("action") == "RECOVER_UNPUBLISHED":
        return {"statusCode": 200, "body": recover_unpublished(
            queue_url, context, int(os.getenv("RECOVERY_LIMIT", "100"))
        )}

    try:
        mailbox = build_mailbox(base_url)
    except GraphAuthError as exc:
        logger.error("Graph authentication failed: %s", exc)
        return {"statusCode": 500, "body": {"error": str(exc)}}

    database = _get_database()
    messages = MessageRepository(database)
    sync_state = SyncStateRepository(database, mailbox.mailbox)

    # Step 2 — previous sync state, STATE-A.
    # The reconciler asks for a full re-read when it finds a mail that was never saved.
    previous_state = None if (event or {}).get("resync") else sync_state.get()
    logger.info("Resuming from a committed sync state: %s", bool(previous_state))

    # Steps 4 and 5 — changed messages and the next sync state, STATE-B.
    try:
        message_ids, next_state = mailbox.changed_message_ids(previous_state)
    except GraphApiError as exc:
        logger.error("Could not read the mailbox: %s", exc)
        return {"statusCode": 502, "body": {"error": str(exc)}}

    received: List[Dict[str, Any]] = []
    already_stored = 0
    failures: List[Dict[str, str]] = []

    for message_id in message_ids:
        try:
            raw = mailbox.get_message(message_id)
            message = normalize(raw)
            detail = {**message.to_dict(), **thread_context(raw)}

            # Steps 6 and 7 — persist, and note whether this message is new.
            if messages.save_if_absent(message.message_id, detail, message.received_at):
                received.append(detail)
            else:
                already_stored += 1
        except Exception as exc:  # one bad message must not stop the batch
            permanent = isinstance(exc, GraphApiError) and exc.is_permanent
            logger.exception("Failed on message %s (%s)", message_id,
                             "permanent" if permanent else "transient")
            failures.append({"message_id": message_id, "error": f"{type(exc).__name__}: {exc}",
                             "permanent": permanent})

    # Step 8 — advance the sync state only once the messages it covers are safe.
    # A message that failed is not covered by the new state, so holding the state
    # back keeps that message on the next run. But a message that fails
    # *permanently* (a 4xx — the request itself is wrong) will fail the same way
    # every run, and holding back for it would freeze the whole pipeline: no new
    # mail would ever be processed. So:
    #   - any transient failure  -> hold back and retry everything next run
    #   - only permanent failures, and nothing succeeded -> hold back too, because
    #     an all-or-nothing failure is almost certainly systemic (a bad request or
    #     config), not one poison message, and advancing would silently drop mail
    #   - permanent failures alongside successes -> skip the poison ones and
    #     advance, so a single bad message cannot block the rest
    transient = [f for f in failures if not f["permanent"]]
    permanent = [f for f in failures if f["permanent"]]
    some_succeeded = (len(received) + already_stored) > 0
    committed = False

    if transient:
        logger.warning("Holding the sync state back; %d transient failure(s) to retry", len(transient))
    elif permanent and not some_succeeded:
        logger.error("Holding the sync state back; all %d message(s) failed permanently "
                     "(treated as systemic, not one bad message, so mail is not dropped)", len(permanent))
    else:
        if permanent:
            logger.error("Skipping %d permanently-failing message(s) and advancing the sync state; "
                         "they cannot be fetched as requested: %s",
                         len(permanent), [f["message_id"] for f in permanent])
        sync_state.commit(next_state)
        committed = True

    # Step 9 — publish to SQS. EventBridge Pipe is responsible for starting SF1.
    # Earlier persisted mails that were not enqueued are the reconciler's, which is
    # bounded by RECONCILE_SINCE, RECONCILE_LIMIT and its retry cap.
    published = 0
    if workflow_enabled:
        published, failed = publish_messages(queue_url, received, context)
        failures += failed
    else:
        logger.info("ENABLE_WORKFLOW=false: SQS publishing skipped")

    body = {
        "changed": len(message_ids),
        "received": len(received),
        "already_stored": already_stored,
        "messages_enqueued": published,
        "sync_state_committed": committed,
        "workflow_enabled": workflow_enabled,
        "cutoff_utc": os.getenv("INGESTION_CUTOFF_UTC", ""),
    }
    if failures:
        body["failures"] = failures
    logger.info("Stage 1 complete: %s", body)
    return {"statusCode": 200, "body": body}
