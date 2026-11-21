"""Handing a mail to a person: the pipeline could not finish it, or should not decide it alone.

Marks the mail and its case in the database. Callers apply the red Outlook label
themselves, since each already has a mailbox connection.
"""

from typing import Any, Dict, Optional

from psycopg.types.json import Jsonb

# A payment mail is investigated only at this confidence or above, only when it asks for
# something the investigation can answer (where a payment is, or its payment details), and
# only when it does not also mention a change of bank details. The intake Lambda applies the rule and the workflow
# reads its answer; the reconciler's query in reconcile.py repeats it and must be kept equal.
MIN_CONFIDENCE = 4
SUPPORTED_REQUESTS = ("PAYMENT_STATUS_REQUEST", "REMITTANCE_ADVICE_REQUEST")

ESCALATE_SQL = """
with c as (
    update ps.case_header set case_status = 'ESCALATED', escalation_reason = %s
    where case_id = %s returning case_id, correlation_id
)
insert into ps.case_event (case_id, correlation_id, event_type, actor_type, actor_id, details)
select case_id, correlation_id, 'NEEDS_ATTENTION', 'SYSTEM', 'pipeline', %s from c
"""


def review_reason(classification: Dict[str, Any]) -> Optional[str]:
    """Why a payment-related mail goes to a person instead of the investigation, or None.

    None means it is investigated, or is not for the desk and needs nobody.
    """
    if not classification.get("is_payment_related"):
        return None
    confidence = classification.get("confidence") or 0
    if confidence < MIN_CONFIDENCE:
        return f"low-confidence classification ({confidence})"
    if classification.get("mentions_account_change"):
        return "mentions a change of bank or payment details"
    if classification.get("request_type") not in SUPPORTED_REQUESTS:
        return f"not a request the investigation can answer ({classification.get('request_type')})"
    return None


def investigates(classification: Dict[str, Any]) -> bool:
    """Whether the mail goes on to extraction and investigation."""
    return bool(classification.get("is_payment_related")) and review_reason(classification) is None


def escalate(database, message_id: str, case_id: Optional[str], reason: str, details: Dict[str, Any]) -> None:
    """Mark the mail as needing attention, and its case ESCALATED with the reason."""
    with database.cursor() as cursor:
        cursor.execute("update ps.messages set status = 'NEEDS_ATTENTION' where message_id = %s",
                       (message_id,))
        if case_id:
            cursor.execute(ESCALATE_SQL, (reason, case_id, Jsonb({**details, "graph_message_id": message_id})))
