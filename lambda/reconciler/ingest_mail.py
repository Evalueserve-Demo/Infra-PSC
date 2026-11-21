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
    9. Start Step Function 1 for each newly received message

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
    STATE_MACHINE_ARN    Step Function 1, started once per new message
    START_INTERVAL_SECONDS  pause between those starts; default 0 (all at once)
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
    """The mail source: CSV files in S3 when MAIL_SOURCE=s3, otherwise Outlook through Graph.

    Step 3 — Graph credentials come from Secrets Manager, never from the environment.
    """
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


def start_step_function(state_machine_arn: str, detail: Dict[str, Any]) -> None:
    boto3.client("stepfunctions").start_execution(
        stateMachineArn=state_machine_arn, input=json.dumps(workflow_input(detail))
    )


def start_workflows(state_machine_arn: str, details: List[Dict[str, Any]], interval: float, context: Any = None):
    """Start Step Function 1 for each mail, one every `interval` seconds.

    Starting a whole burst at once puts every workflow on the account's Lambda
    concurrency limit in the same moment. Stops when the Lambda is nearly out of
    time; the mails left over stay RECEIVED and the reconciler starts them.
    """
    started, failures = 0, []
    for detail in details:
        if context and context.get_remaining_time_in_millis() < 30_000:
            logger.warning("Nearly out of time; the remaining mails are left for the reconciler")
            break
        try:
            start_step_function(state_machine_arn, detail)
            started += 1
        except Exception as exc:
            logger.exception("Could not start Step Function 1 for %s", detail["message_id"])
            failures.append({"message_id": detail["message_id"], "error": f"StartExecution: {exc}"})
        time.sleep(interval)
    return started, failures


def lambda_handler(event: Dict[str, Any] | None = None, context: Any = None) -> Dict[str, Any]:
    base_url = "" if uses_s3() else os.environ["GRAPH_API_BASE_URL"]
    state_machine_arn = os.getenv("STATE_MACHINE_ARN")

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

    # Step 9 — hand each new message to Step Function 1.
    started = 0
    if state_machine_arn:
        started, failed = start_workflows(
            state_machine_arn, received, float(os.getenv("START_INTERVAL_SECONDS", "0")), context)
        failures += failed

    body = {
        "changed": len(message_ids),
        "received": len(received),
        "already_stored": already_stored,
        "executions_started": started,
        "sync_state_committed": committed,
    }
    if failures:
        body["failures"] = failures
    logger.info("Stage 1 complete: %s", body)
    return {"statusCode": 200, "body": body}
