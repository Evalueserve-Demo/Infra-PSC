"""STAGES 5 and 6 — invoking the intake agents.

Step Function 2 calls this once per stage, naming the role it wants. Keeping it
in one function rather than two means one deployment and one place where the
case context is enriched, while the state machine still shows the stages
separately.

The agents live on AgentCore Runtime and return structured results. This adds
them to the case and hands it back; it makes no decisions of its own, other
than the deterministic invoice-format check, which belongs in code rather than
in a model.

When the email has attachments, both agents also read their text, screened by
the guardrail first (see intake/attachments.py): the classifier an excerpt of
each, enough to recognise an invoice, and the extractor all of it, because the
numbers are often only on the attached invoice. An email without attachments
is laid out exactly as before.

Environment:
    CLASSIFICATION_RUNTIME_ARN   Stage 5
    EXTRACTION_RUNTIME_ARN       Stage 6
    ATTACHMENT_BUCKET            where Stage 2 left the attachment text
"""

import json
import logging
import os
import re
import time
import uuid
from typing import Any, Dict, List

import boto3
from psycopg.types.json import Jsonb

import metrics
from config import resolve_intake_model_id
from escalation import escalate, investigates, review_reason
import attachments as attachment_inputs
import prompts
from guardrail import screen, withheld
from categories import (
    NEEDS_ATTENTION, SPAM, CategoryError, MailboxCategories, category_for, labels_enabled,
)
from graph_auth import GraphTokenProvider, load_credentials
from database import (
    AgentCallLogRepository, CaseRepository, Database, IdentifierRepository,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Mirrors the rules Lambda, so what Stage 6 accepts and what the investigation
# agent accepts cannot drift apart.
INVOICE_PATTERN = re.compile(r"^INV-\d{4,10}$")

TAGS = re.compile(r"<[^>]+>")
WHITESPACE = re.compile(r"\s+")

RUNTIME_ARNS = {
    "classification": "CLASSIFICATION_RUNTIME_ARN",
    "extraction": "EXTRACTION_RUNTIME_ARN",
}

# What each runtime is told, kept for the audit record of every call.
AGENT_PROMPTS = {
    "classification": (prompts.CLASSIFICATION_PROMPT, prompts.CLASSIFICATION_OUTPUT_INSTRUCTION),
    "extraction": (prompts.EXTRACTION_PROMPT, prompts.EXTRACTION_OUTPUT_INSTRUCTION),
}

CLASSIFICATION_SQL = """
insert into ps.case_event
    (case_id, correlation_id, event_type, actor_type, actor_id, details)
select case_id, correlation_id, 'CLASSIFICATION_RESULT', %s, %s, %s
from ps.case_header
where case_id = %s
"""

_database: Database | None = None


def _get_database() -> Database:
    global _database
    if _database is None:
        _database = Database()
    return _database


def plain_text(body: str) -> str:
    """Strip the HTML Outlook sends, so the agent reads what a person would."""
    return WHITESPACE.sub(" ", TAGS.sub(" ", body or "")).strip()


def _record_call(role: str, payload: Dict[str, Any], case_id: str, started: float,
                 answer: Dict[str, Any], error: Exception | None) -> None:
    """Keep what the agent was told and answered. Best effort: it never changes the outcome of the call."""
    system_prompt, instruction = AGENT_PROMPTS[role]
    AgentCallLogRepository(_get_database()).try_record(
        case_id=case_id, component="intake", invoked_by=os.getenv("AWS_LAMBDA_FUNCTION_NAME", "email-intake-agents"),
        operation=role, model_id=answer.get("model_id") or resolve_intake_model_id(),
        system_prompt=system_prompt, user_input=answer.get("input") or payload.get("text") or payload.get("body"),
        structured_output_instruction=instruction, output_schema=answer.get("output_schema") or role,
        outcome="FAILURE" if error else "SUCCESS", latency_ms=round((time.monotonic() - started) * 1000),
        response_payload={k: v for k, v in answer.items() if k not in ("input", "output_schema")} or None,
        error=error,
    )


def invoke_runtime(role: str, payload: Dict[str, Any], case_id: str) -> Dict[str, Any]:
    arn = os.environ[RUNTIME_ARNS[role]]
    client = boto3.client("bedrock-agentcore", region_name=os.getenv("AWS_REGION", "us-east-1"))
    started, answer, error = time.monotonic(), {}, None
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=arn,
            runtimeSessionId=f"intake-{role}-{case_id}-{uuid.uuid4()}",
            payload=json.dumps({**payload, "role": role}).encode(),
        )
        answer = json.loads(response["response"].read().decode("utf-8"))
        return answer
    except Exception as exc:
        error = exc
        raise
    finally:
        _record_call(role, payload, case_id, started, answer, error)


def choose_identifiers(extraction: Dict[str, Any]) -> Dict[str, Any]:
    """Pick the invoice to investigate, and say so rather than guessing quietly.

    The flow document leaves multiple-invoice handling open. Until it is
    decided, the first well-formed invoice is used and every candidate is kept
    alongside it, so nothing is lost and the choice is visible.
    """
    pairs: List[Dict[str, Any]] = extraction.get("payment_info") or []
    valid = [p for p in pairs if INVOICE_PATTERN.match((p.get("invoice_number") or "").strip())]
    malformed = [
        p.get("invoice_number")
        for p in pairs
        if p.get("invoice_number") and not INVOICE_PATTERN.match(p["invoice_number"].strip())
    ]

    # The same invoice usually appears more than once: once in the subject with
    # no purchase order, and again in the body with one. Taking the first pair
    # would silently drop the purchase order, so the pairs for an invoice are
    # merged and the first purchase order found for it is kept.
    chosen = valid[0] if valid else {}
    invoice = chosen.get("invoice_number")
    po = next(
        (p.get("po_number") for p in valid if p.get("invoice_number") == invoice and p.get("po_number")),
        None,
    )

    return {
        "invoice_number": invoice,
        "po_number": po,
        "identifier_valid": bool(valid),
        "malformed_identifiers": malformed,
        "candidates": pairs,
        "multiple_invoices": len({p.get("invoice_number") for p in valid}) > 1,
    }


def _mark_in_outlook(message_id: str, category: str) -> str | None:
    """Add a coloured label to the message.

    A failure here is logged and swallowed. The classification is already made
    and the case should carry on to investigation; losing a label is not a
    reason to fail the workflow.
    """
    if not labels_enabled():
        return None
    try:
        credentials = load_credentials(os.environ["GRAPH_SECRET_ID"])
        categories = MailboxCategories(
            os.environ["GRAPH_API_BASE_URL"],
            os.getenv("GRAPH_MAILBOX") or credentials["mailbox"],
            GraphTokenProvider(credentials),
        )
        categories.ensure_categories()
        categories.apply(message_id, category)
        logger.info("Marked the message in Outlook as %r", category)
        return category
    except (CategoryError, KeyError) as exc:
        logger.warning("Could not mark the message in Outlook: %s", exc)
        return None


def _record_classification(
    case: Dict[str, Any],
    classification: Dict[str, Any],
    mailbox_category: str | None,
    guardrail: Dict[str, Any],
    actor_type: str,
    actor_id: str,
) -> bool:
    """Keep the classification on the case's audit trail.

    Outlook shows only the label, and the Step Functions history expires, so
    this is the lasting record of why a mail was or was not investigated. The
    mail is identified by its Graph message ID, because a conversation can hold
    several messages and each is classified on its own.

    Best effort, as with the Outlook label: the classification is already made,
    and a failed write is not a reason to fail the workflow.
    """
    case_id = case.get("case_id")
    try:
        uuid.UUID(str(case_id))
    except ValueError:
        return False
    try:
        with _get_database().cursor() as cursor:
            cursor.execute(
                CLASSIFICATION_SQL,
                (
                    actor_type,
                    actor_id,
                    Jsonb({
                        "graph_message_id": case.get("message_id"),
                        "classification": classification,
                        "mailbox_category": mailbox_category,
                        "guardrail": guardrail,
                    }),
                    case_id,
                ),
            )
        return True
    except Exception as exc:
        logger.warning("%s could not record the classification: %s", case_id, exc)
        return False


def _close_if_not_ours(case_id: str, classification: Dict[str, Any]) -> None:
    """A mail the desk has nothing to answer is finished here, so NEW only ever means "not processed yet"."""
    if not classification.get("is_payment_related") and case_id != "unknown":
        CaseRepository(_get_database()).mark_out_of_scope(case_id)


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    role = event["role"]
    case = event["case"]
    case_id = case.get("case_id", "unknown")
    message_id = case["message_id"]
    body = plain_text(case.get("body"))

    if role == "classification":
        started = time.monotonic()
        # Stage 4 — screen the received email before the model reads it. A
        # blocked email is never classified; it is marked and diverted so a
        # person looks at it. The injection attempt does not reach the model.
        guard = screen(case.get("subject", ""), body)
        if guard.blocked:
            logger.warning("%s blocked by guardrail: %s", case_id, guard.reason)
            category = _mark_in_outlook(message_id, SPAM)
            elapsed = time.monotonic() - started
            metrics.seconds("EmailProcessingSeconds", elapsed, stage="classification")
            metrics.count("GuardrailBlocked")
            logger.info("%s classification took %.1fs (blocked)", case_id, elapsed)
            blocked = withheld("the email was blocked by the content guardrail")
            _record_classification(case, blocked, category, guard.as_dict(), "SYSTEM", "inbound-guardrail")
            _close_if_not_ours(case_id, blocked)
            return {
                **case,
                "guardrail": guard.as_dict(),
                "classification": blocked,
                "investigate": False,
                "mailbox_category": category,
                "seconds": round(elapsed, 2),
            }

        payload = {"subject": case.get("subject", ""), "body": body, "sender": case.get("sender", "")}
        attached = attachment_inputs.screened(case.get("attachments") or [])
        if attached:
            payload["text"] = attachment_inputs.classification_text(
                payload["subject"], body, payload["sender"], attached
            )
        answer = invoke_runtime(role, payload, case_id)
        result = answer.get("classification") or {}
        elapsed = time.monotonic() - started
        metrics.seconds("EmailProcessingSeconds", elapsed, stage="classification")
        logger.info(
            "%s classified as %s (%s confidence) in %.1fs",
            case_id,
            result.get("request_type"),
            result.get("confidence"),
            elapsed,
        )
        category = _mark_in_outlook(message_id, category_for(result))
        _record_classification(case, result, category, guard.as_dict(), "AGENT", "email-classification-agent")
        _close_if_not_ours(case_id, result)
        if reason := review_reason(result):
            # Too unsure, or not a request the investigation answers: a person decides, instead of the mail stopping unseen.
            escalate(_get_database(), message_id, case_id if case_id != "unknown" else None, reason,
                     {"classification": result.get("request_type"), "confidence": result.get("confidence")})
            _mark_in_outlook(message_id, NEEDS_ATTENTION)
        return {**case, "classification": result, "investigate": investigates(result), "mailbox_category": category,
                "guardrail": guard.as_dict(), "seconds": round(elapsed, 2),
                "attachments_read": attachment_inputs.summary(attached)}

    if role == "extraction":
        if not isinstance(case_id, str) or case_id == "unknown":
            raise ValueError("Identifier persistence requires a case UUID")
        payload = {"subject": case.get("subject", ""), "body": body}
        attached = attachment_inputs.screened(case.get("attachments") or [])
        if attached:
            payload["text"] = attachment_inputs.extraction_text(payload["subject"], body, attached)
        answer = invoke_runtime(role, payload, case_id)
        result = answer.get("extraction") or {}
        identifiers = choose_identifiers(result)
        stored_identifiers = IdentifierRepository(_get_database()).save_candidates(
            case_id, identifiers["candidates"]
        )
        logger.info(
            "%s identifiers: invoice=%s po=%s valid=%s persisted=%d",
            case_id,
            identifiers["invoice_number"],
            identifiers["po_number"],
            identifiers["identifier_valid"],
            stored_identifiers,
        )
        return {**case, "identifiers": identifiers}

    raise ValueError(f"Unknown role {role!r}")
