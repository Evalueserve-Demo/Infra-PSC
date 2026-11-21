"""The reconciler: every mail must reach a final state, with nobody checking.

Runs on a schedule. For each mail older than 15 minutes it finds where, if
anywhere, the mail stopped short, and sends it back to that step:

    no case opened                         start Workflow 1 again
    not classified                         put it on Queue 1, so Workflow 2 runs again
    payment-related, not investigated      the same

A retried mail keeps its attachments. Its Aurora row records only that it has
some, not which, so a mail with no case gets its attachment list from Graph
again, and one that already has a case gets the attachments Stage 2 recorded.
Without that the retry would carry on as if the mail had none.

A mail still stuck after MAX_ATTEMPTS is not retried again. It is marked as
needing attention, labelled in Outlook and recorded on its case, so a person
sees it. Last, the mails the last read should have saved are compared with what is stored, and
the mailbox is re-read when one is missing.

Each run publishes how many mails are unfinished, retried and escalated, for
alarms. Retries are paced (START_INTERVAL_SECONDS) and capped at RECONCILE_LIMIT per
run, and a run stops before its Lambda times out, so recovery is never a burst of its own.

Environment:
    AURORA_HOST, AURORA_DB, AURORA_USER   the database
    MAIL_SOURCE, MAIL_S3_BUCKET, MAIL_S3_PREFIX   graph (default) or s3, and where the CSV files are
    GRAPH_API_BASE_URL, GRAPH_SECRET_ID   the mailbox
    STATE_MACHINE_ARN                     Step Function 1
    SQS1_QUEUE_URL                        Queue 1, in front of Step Function 2
    INGESTION_FUNCTION                    the ingestion Lambda, re-run for a full read
    RECONCILE_SINCE                       ignore mails ingested before this (ISO time); default one day
    INGESTION_CUTOFF_UTC                  the ingestion cutoff, if it has one: older mail is not "missed"
    RECONCILE_LIMIT                       mails retried per run; default 100
    START_INTERVAL_SECONDS                pause between retries; default 0
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import boto3

import metrics
from escalation import MIN_CONFIDENCE, SUPPORTED_REQUESTS, escalate
from ingest_mail import build_mailbox, start_step_function
from open_case import normalized_context
from categories import NEEDS_ATTENTION, CategoryError, MailboxCategories, labels_enabled
from graph_client import GraphApiError, cutoff_from_env, thread_context
from database import CaseRepository, Database, MessageRepository

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MAX_ATTEMPTS = 3
# Graph can take a moment to list a mail it has just received, so a read is trusted only up to here.
READ_MARGIN = timedelta(minutes=5)

NO_CASE = "no case opened"
UNCLASSIFIED = "not classified"
UNINVESTIGATED = "payment-related but not investigated"

# Mails past the 15-minute grace, not already given up on, and not retried in the last 15 minutes.
STATE_SQL = """
select m.message_id, m.attempts, cm.case_id::text as case_id,
       cls.details is not null as classified,
       coalesce((cls.details -> 'classification' ->> 'is_payment_related')::bool, false)
           and coalesce((cls.details -> 'classification' ->> 'confidence')::int, 0) >= %s
           and cls.details -> 'classification' ->> 'request_type' = any(%s)
           and not coalesce((cls.details -> 'classification' ->> 'mentions_account_change')::bool, false) as payment,
       inv.event_id is not null as investigated
from ps.messages m
left join ps.case_message cm on cm.graph_message_id = m.message_id
left join lateral (
    select e.details from ps.case_event e
    where e.case_id = cm.case_id and e.event_type = 'CLASSIFICATION_RESULT'
      and e.details ->> 'graph_message_id' = m.message_id
    order by e.occurred_at desc limit 1
) cls on true
left join lateral (
    select e.event_id from ps.case_event e
    where e.case_id = cm.case_id and e.event_type = 'INVESTIGATION_RESULT' limit 1
) inv on true
where m.ingested_at >= %s and m.ingested_at < now() - interval '15 minutes'
  and m.status <> 'NEEDS_ATTENTION'
  and (m.attempted_at is null or m.attempted_at < now() - interval '15 minutes')
order by m.ingested_at
"""

_database: Database | None = None


def _get_database() -> Database:
    global _database
    if _database is None:
        _database = Database()
    return _database


def stage(row: Dict[str, Any]) -> Optional[str]:
    """Where the mail stopped, or None when it has finished."""
    if row["case_id"] is None:
        return NO_CASE
    if not row["classified"]:
        return UNCLASSIFIED
    if row["payment"] and not row["investigated"]:
        return UNINVESTIGATED
    return None


def plan(rows: List[Dict[str, Any]], limit: int) -> Tuple[list, list, list]:
    """The unfinished mails, those to retry (oldest first, up to limit), and those to give up on."""
    gaps = [(row, where) for row in rows if (where := stage(row))]
    retry = [g for g in gaps if g[0]["attempts"] < MAX_ATTEMPTS][:limit]
    escalate = [g for g in gaps if g[0]["attempts"] >= MAX_ATTEMPTS]
    return gaps, retry, escalate


def with_attachments(mailbox: Any, detail: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A mail about to restart Workflow 1, with its attachment list read from Graph again.

    None when the list cannot be read: the mail waits for the next run rather
    than restarting without its attachments, and is escalated if it never can.
    """
    if not detail.get("has_attachment"):
        return detail
    try:
        return {**detail, **thread_context(mailbox.get_message(detail["message_id"]))}
    except GraphApiError as exc:
        logger.warning("Not retrying %s; its attachments could not be listed: %s", detail["message_id"], exc)
        return None


def missed(mailbox, stored: set, committed_at: Optional[datetime], cutoff: Optional[datetime] = None) -> set:
    """Mails received before the last successful read that are still not saved.

    A newer mail is just waiting for the next scheduled read, which collects it. Treating it as
    missed starts a second read on top of the scheduled one and doubles the load. A mail older than
    the ingestion cutoff was never meant to be saved, so it is not missed either.
    """
    if committed_at is None:
        return set()
    return set(mailbox.message_ids_before(committed_at - READ_MARGIN, since=cutoff)) - stored


def _retry(row: Dict[str, Any], where: str, state_machine_arn: str, queue_url: str, mailbox: Any) -> None:
    detail = MessageRepository(_get_database()).get_staged_message(row["message_id"])
    if where == NO_CASE:
        detail = with_attachments(mailbox, detail)
        if detail is not None:
            start_step_function(state_machine_arn, detail)
    else:
        detail = {**detail, "attachments": CaseRepository(_get_database()).attachments_of(row["message_id"])}
        boto3.client("sqs").send_message(
            QueueUrl=queue_url, MessageBody=json.dumps(normalized_context(detail, row["case_id"]))
        )
    # Counted even when the mail could not be restarted, so one that never can is escalated.
    with _get_database().cursor() as cursor:
        cursor.execute("update ps.messages set attempts = attempts + 1, attempted_at = now() "
                       "where message_id = %s", (row["message_id"],))


def _escalate(row: Dict[str, Any], where: str, categories: Optional[MailboxCategories]) -> None:
    escalate(_get_database(), row["message_id"], row["case_id"], f"{where}; retried {row['attempts']} times",
             {"stage": where, "attempts": row["attempts"]})
    if categories is None:  # mail comes from S3: no Outlook to label
        return
    try:
        categories.apply(row["message_id"], NEEDS_ATTENTION)
    except CategoryError as exc:
        logger.warning("Could not label %s in Outlook: %s", row["message_id"], exc)


def lambda_handler(event: Any = None, context: Any = None) -> Dict[str, Any]:
    since = os.getenv("RECONCILE_SINCE") or (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with _get_database().cursor() as cursor:
        cursor.execute(STATE_SQL, (MIN_CONFIDENCE, list(SUPPORTED_REQUESTS), since))
        names = [c.name for c in cursor.description]
        rows = [dict(zip(names, record)) for record in cursor.fetchall()]
    gaps, retry, escalate = plan(rows, int(os.getenv("RECONCILE_LIMIT", "100")))

    mailbox = build_mailbox(os.getenv("GRAPH_API_BASE_URL", ""))
    categories = (MailboxCategories(mailbox.base_url, mailbox.mailbox, mailbox.token_provider)
                  if labels_enabled() else None)
    for row, where in retry:
        if context and context.get_remaining_time_in_millis() < 60_000:
            break  # the rest are retried on the next run
        _retry(row, where, os.environ["STATE_MACHINE_ARN"], os.environ["SQS1_QUEUE_URL"], mailbox)
        time.sleep(float(os.getenv("START_INTERVAL_SECONDS", "0")))
    for row, where in escalate:
        _escalate(row, where, categories)

    # A mail that was in the inbox at the last read but never saved is invisible to everything above.
    with _get_database().cursor() as cursor:
        cursor.execute("select committed_at from ps.graph_sync_state where mailbox = %s", (mailbox.mailbox,))
        read = cursor.fetchone()
        cursor.execute("select message_id from ps.messages")
        stored = {record[0] for record in cursor.fetchall()}
    unsaved = missed(mailbox, stored, read[0] if read else None, cutoff_from_env())
    if unsaved:
        boto3.client("lambda").invoke(FunctionName=os.environ["INGESTION_FUNCTION"], InvocationType="Event",
                                      Payload=json.dumps({"resync": True}).encode())

    summary = {"unfinished": len(gaps), "retried": len(retry), "escalated": len(escalate),
               "not_saved": len(unsaved)}
    for name, value in summary.items():
        metrics.put(f"Reconciler{name.title().replace('_', '')}", value)
    logger.info("Reconciled: %s", summary)
    return summary
