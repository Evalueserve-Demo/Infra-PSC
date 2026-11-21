"""STAGE 7/8 — the payment investigation invocation Lambda (multi-invoice).

Invoked by an EventBridge Pipe reading SQS2. A case can name several invoices
(one email listing three, say). Each is investigated on its own — its own agent
call — and then one supplier reply is composed covering all of them, because
they arrived in one email and should be answered in one.

Persistence follows the ps case store: the case outcome and routing are rolled
up on ps.case_header, and the full per-invoice detail is recorded as a
ps.case_event. (Per-invoice rows in ps.case_investigation / ps.invoice_finding
are available for richer persistence later; the detail is kept in the event
payload for now.)

Export control is applied per invoice: an export-controlled invoice contributes
no detail to the shared reply, and if any invoice in the case is controlled the
whole case routes to the US Helpdesk.

Raising on failure is deliberate: the Pipe then follows its own retry and DLQ
path rather than this Lambda deciding what a failure means.

Environment:
    AGENT_RUNTIME_ARN   the payment investigation agent to invoke
    AURORA_HOST         cluster endpoint
    AURORA_DB           database name, defaults to appdb
    AURORA_USER         database user, defaults to integ_app
"""

import json
import logging
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

import boto3
from psycopg.types.json import Jsonb

from config import resolve_model_id
from prompt import INVESTIGATION_OUTPUT_INSTRUCTION, PAYMENT_AGENT_PROMPT
from database import AgentCallLogRepository, Database
from categories import DRAFT_READY, IN_PROGRESS, MailboxCategories, labels_enabled
from graph_auth import GraphTokenProvider, load_credentials

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AgentCore requires a session identifier of at least 33 characters.
SESSION_PREFIX = "payment-investigation-"

# Mirrors the intake format check, so what Stage 6 accepts and what this
# investigates cannot drift apart.
INVOICE_PATTERN = re.compile(r"^INV-\d{4,10}$")

# How many times the supplier may be asked for more before the case stops
# auto-replying and is handed to a person. Past this, a still-unresolved invoice
# is escalated to the helpdesk rather than asked a third time.
MAX_ASK_ROUNDS = 2

# Outcome codes (ps OUTCOME domain) that mean "could not resolve, would have to
# ask the supplier for more". The round cap only escalates on these.
NEEDS_MORE_INFO = {"NOT_FOUND", "DATA_INCOMPLETE"}

STORE_SQL = """
with updated_case as (
    update ps.case_header
    set case_status = %s,
        outcome_code = coalesce(%s, outcome_code),
        is_itar = coalesce(%s, is_itar),
        assigned_queue = coalesce(%s, assigned_queue),
        summary = coalesce(summary, %s)
    where case_id = %s
    returning case_id, correlation_id
)
insert into ps.case_event
    (case_id, correlation_id, event_type, actor_type, actor_id, details)
select case_id, correlation_id, 'INVESTIGATION_RESULT', 'AGENT',
       'payment-investigation-agent', %s
from updated_case
returning event_id
"""

ROUNDS_SQL = "select count(*) from ps.case_message where case_id = %s"

_database: Database | None = None


def _get_database() -> Database:
    global _database
    if _database is None:
        _database = Database()
    return _database


def valid_invoices(case: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The well-formed invoices to investigate, from the case payload.

    Stage 6 carries every extracted candidate on the case; here they are
    filtered to the well-formed ones and de-duplicated, keeping the first
    purchase order seen for each invoice. Falls back to the single chosen
    identifier if there are no candidates.
    """
    identifiers = case.get("identifiers") or {}
    seen: Dict[str, Dict[str, Any]] = {}
    for pair in identifiers.get("candidates") or []:
        invoice = (pair.get("invoice_number") or "").strip()
        if not INVOICE_PATTERN.match(invoice):
            continue
        if invoice not in seen:
            seen[invoice] = {"invoice_number": invoice, "po_number": pair.get("po_number")}
        elif not seen[invoice]["po_number"] and pair.get("po_number"):
            seen[invoice]["po_number"] = pair.get("po_number")
    if seen:
        return list(seen.values())
    if identifiers.get("invoice_number"):
        return [{"invoice_number": identifiers["invoice_number"], "po_number": identifiers.get("po_number")}]
    return []


def itar_flag(case: Dict[str, Any]) -> bool | None:
    """The export-control flag the ticket carried, or None when it gave none.

    It is an input to the investigation rather than something the agent looks
    up, and redaction and routing depend on it, so the case value is applied to
    the result directly instead of trusting the agent to repeat it.
    """
    value = case.get("itar_flag")
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip():
        return value.strip().lower() in {"yes", "true", "y", "1"}
    return None


def itar_routed(result: Dict[str, Any]) -> bool:
    """Does this invoice's ITAR flag decide the redaction and the helpdesk?

    A NOT_FOUND invoice goes straight to the TCS Helpdesk whatever the flag says,
    and its reply says only that the invoice was not found.
    """
    return bool(result.get("itar_controlled")) and result.get("outcome") != "NOT_FOUND"


def build_question(case: Dict[str, Any], invoice: str, po: str | None) -> str:
    """Ask the agent about one specific invoice, with the email for context."""
    flag = itar_flag(case)
    lines = [
        "A supplier has sent the following payment query. Investigate it.",
        "",
        f"Invoice under investigation: {invoice}",
        f"Purchase order: {po or 'not provided'}",
        f"ITAR flag from the ticket: {'not provided' if flag is None else 'Yes' if flag else 'No'}",
    ]
    lines += [
        "",
        "The email may mention other invoices; investigate only the one named above.",
        "",
        f"From: {case.get('sender')}",
        f"Subject: {case.get('subject')}",
        "",
        str(case.get("body") or ""),
    ]
    return "\n".join(lines)


def invoke_agent(question: str, case_id: str, session_key: str) -> Dict[str, Any]:
    """Ask the deployed agent, and return its parsed response.

    Every call leaves a ps.integration_call_log record, so the prompt, answer, model and
    timing can be reviewed per case. The record is best effort and never changes the outcome.
    """
    client = boto3.client("bedrock-agentcore", region_name=os.getenv("AWS_REGION", "us-east-1"))
    started, answer, schema, error = time.monotonic(), {}, None, None
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=os.environ["AGENT_RUNTIME_ARN"],
            runtimeSessionId=f"{SESSION_PREFIX}{session_key}-{uuid.uuid4()}",
            payload=json.dumps({"prompt": question, "structured": True}).encode(),
        )
        answer = json.loads(response["response"].read().decode("utf-8"))
        schema = answer.pop("output_schema", None)
        return answer
    except Exception as exc:
        error = exc
        raise
    finally:
        AgentCallLogRepository(_get_database()).try_record(
            case_id=case_id, component="investigation",
            invoked_by=os.getenv("AWS_LAMBDA_FUNCTION_NAME", "payment-investigation-invocation"),
            operation="investigate", model_id=answer.get("model") or resolve_model_id(),
            system_prompt=PAYMENT_AGENT_PROMPT, user_input=question,
            structured_output_instruction=INVESTIGATION_OUTPUT_INSTRUCTION, output_schema=schema or "Investigation",
            outcome="FAILURE" if error else "SUCCESS", latency_ms=round((time.monotonic() - started) * 1000),
            response_payload=answer or None, error=error,
        )


def _plain_outcome(outcome: str) -> str:
    return {
        "PAID": "This invoice has been paid",
        "SCHEDULED": "This invoice is reconciled and scheduled for payment",
        "NOT_YET_DUE": "This invoice is scheduled and not yet due",
        "OVERDUE": "This invoice is overdue and under review",
        "REJECTED": "This invoice was rejected in our system",
        "RECONCILING": "This invoice has been received and is being reconciled",
        "RECEIPT_PENDING": "This invoice is awaiting confirmation of the goods receipt",
        "MISMATCH_FOUND": "This invoice is on hold while a purchase-order mismatch is reviewed",
        "NOT_FOUND": "We could not locate this invoice in our systems",
        "DATA_INCOMPLETE": "We need more information before we can resolve this invoice",
    }.get(outcome, "This invoice is under review")


def _detail_lines(result: Dict[str, Any]) -> List[str]:
    """What a paid or scheduled invoice adds to the reply: the payment details, or the due date."""
    lines: List[str] = []
    if result.get("outcome") == "PAID":
        if result.get("transaction_id"):
            lines.append(f"  Payment transaction: {result['transaction_id']}")
        details = result.get("transaction_details") or {}
        for key, label in (("amount_paid", "Amount paid"), ("payment_date", "Payment date"),
                           ("payment_method", "Payment method"), ("reference", "Reference #"),
                           ("originating_bank", "Originating bank"), ("receiving_bank", "Receiving bank")):
            if details.get(key):
                lines.append(f"  {label}: {details[key]}")
    elif result.get("outcome") == "SCHEDULED" and result.get("due_date"):
        lines.append(f"  Due date: {result['due_date']}")
    return lines


def combined_reply(per_invoice: List[Dict[str, Any]]) -> str:
    """One supplier reply covering every invoice, redacted per invoice.

    An export-controlled invoice contributes no order detail; an escalated one is
    handed to the helpdesk; the rest carry their finding. The reply is assembled
    from each invoice's structured result rather than concatenating full drafts,
    so nothing restricted slips through.
    """
    intro = ("Here is the status of your invoice:" if len(per_invoice) == 1
             else "Here is the status of each invoice you raised:")
    lines = ["Dear Supplier,", "", f"Thank you for your query. {intro}"]
    for item in per_invoice:
        inv = item["invoice_number"]
        result = item.get("result") or {}
        if itar_routed(result):
            lines += ["", f"- {inv}: Because this order is subject to export-control restrictions, "
                          "we cannot set out the specifics of its status here; your Boeing "
                          "representative will be in touch."]
        elif item.get("escalated"):
            lines += ["", f"- {inv}: We have not been able to resolve this from the details provided "
                          "so far. To avoid further back-and-forth, we have referred it to our "
                          "helpdesk, and a member of the team will contact you directly."]
        else:
            outcome = result.get("outcome") or "under review"
            action = result.get("next_action") or ""
            detail = f" {action}" if action else ""
            lines += ["", f"- {inv}: {_plain_outcome(outcome)}.{detail}"] + _detail_lines(result)
    lines += ["", "A member of our team will confirm this reply before it is sent.",
              "", "Kind regards,", "Boeing Supplier Payments"]
    return "\n".join(lines)


def no_invoice_reply() -> str:
    """Draft for a payment email we could not tie to an invoice."""
    return "\n".join([
        "Dear Supplier,",
        "",
        "Thank you for your query. We were not able to find an invoice number in "
        "your message, so we could not look into it yet.",
        "",
        "Please reply with the invoice number (for example INV-12345), and the "
        "purchase order number if you have it, and we will investigate right away.",
        "",
        "A member of our team will confirm this reply before it is sent.",
        "",
        "Kind regards,",
        "Boeing Supplier Payments",
    ])


def _rounds(case_id: str) -> int:
    """How many messages the conversation has had, i.e. how many times the
    supplier has written in. Tolerant of any error (treated as round 1)."""
    try:
        with _get_database().cursor() as cursor:
            cursor.execute(ROUNDS_SQL, (case_id,))
            return max(1, int(cursor.fetchone()[0]))
    except Exception as exc:
        logger.warning("%s could not read round count, treating as round 1: %s", case_id, exc)
        return 1


def store(case: Dict[str, Any], overall: Dict[str, Any], per_invoice: List[Dict[str, Any]]) -> None:
    """Roll the case up on ps.case_header and record the full detail as an event."""
    outcomes = [i["result"].get("outcome") for i in per_invoice if i.get("result")]
    # The agent already returns ps OUTCOME codes, so a single-invoice case sets the
    # case-level outcome directly; several invoices keep their outcomes per invoice
    # in the event detail instead.
    outcome_code = outcomes[0] if len(outcomes) == 1 else None
    with _get_database().cursor() as cursor:
        cursor.execute(
            STORE_SQL,
            (
                "ESCALATED" if overall["escalated"] else "PENDING_REVIEW",
                outcome_code,
                overall["itar_controlled"],
                "US_HELPDESK" if overall["helpdesk"] == "US Helpdesk" else "TCS_HELPDESK",
                overall.get("summary"),
                case["case_id"],
                Jsonb({
                    "per_invoice": per_invoice,
                    "draft_reply": overall["draft_reply"],
                    "escalated": overall["escalated"],
                    "round": overall["round"],
                }),
            ),
        )
        if cursor.fetchone() is None:
            raise ValueError(f"Case {case['case_id']} was not found")


def _is_sqs_record(value: Any) -> bool:
    return isinstance(value, dict) and "body" in value and (
        "messageId" in value or "receiptHandle" in value or "eventSourceARN" in value
    )


def _records(event: Any) -> List[Dict[str, Any]]:
    if isinstance(event, dict) and "Records" in event:
        event = event["Records"]
    if isinstance(event, list):
        return [json.loads(r["body"]) if _is_sqs_record(r) else r for r in event]
    if _is_sqs_record(event):
        return [json.loads(event["body"])]
    return [event]


def _mark_outlook(message_id: str | None, category: str) -> None:
    """Add a coloured label to the mail. Best effort — a failure is swallowed."""
    if not message_id or not labels_enabled() or not os.getenv("GRAPH_SECRET_ID") or not os.getenv("GRAPH_API_BASE_URL"):
        return
    try:
        credentials = load_credentials(os.environ["GRAPH_SECRET_ID"])
        categories = MailboxCategories(
            os.environ["GRAPH_API_BASE_URL"],
            os.getenv("GRAPH_MAILBOX") or credentials["mailbox"],
            GraphTokenProvider(credentials),
        )
        categories.ensure_categories()
        categories.apply(message_id, category)
        logger.info("Marked %s in Outlook as %r", message_id, category)
    except Exception as exc:
        logger.warning("Could not mark %s in Outlook as %r: %s", message_id, category, exc)


def lambda_handler(event: Any, context: Any = None) -> Dict[str, Any]:
    handled = []

    for case in _records(event):
        case_id = case["case_id"]
        rounds = _rounds(case_id)
        targets = valid_invoices(case)
        logger.info("Investigating %s across %d invoice(s), round %d", case_id, len(targets), rounds)
        _mark_outlook(case.get("message_id"), IN_PROGRESS)

        # Each invoice is an independent agent call, so they run concurrently:
        # three invoices that took three turns in series now take about one.
        def _ask(target: Dict[str, Any]) -> Dict[str, Any]:
            invoice = target["invoice_number"]
            return invoke_agent(build_question(case, invoice, target.get("po_number")), case_id, f"{case_id}-{invoice}")

        if targets:
            with ThreadPoolExecutor(max_workers=min(len(targets), 5)) as pool:
                answers = list(pool.map(_ask, targets))
        else:
            answers = []

        per_invoice = []
        for target, answer in zip(targets, answers):
            invoice = target["invoice_number"]
            result = answer.get("result") or {}
            flag = itar_flag(case)
            if flag is not None:
                result["itar_controlled"] = flag
            outcome = result.get("outcome") or "UNKNOWN"
            escalated = outcome in NEEDS_MORE_INFO and rounds > MAX_ASK_ROUNDS
            per_invoice.append({"invoice_number": invoice, "result": result,
                                "escalated": escalated, "narrative": answer.get("narrative")})
            logger.info("  %s -> %s (itar=%s)%s", invoice, outcome,
                        result.get("itar_controlled"), " [ESCALATED]" if escalated else "")

        any_itar = any(itar_routed(i["result"] or {}) for i in per_invoice)
        any_escalated = any(i["escalated"] for i in per_invoice)
        overall = {
            "helpdesk": "US Helpdesk" if any_itar else "TCS Helpdesk",
            "itar_controlled": any_itar,
            "escalated": any_escalated,
            "round": rounds,
            "summary": f"Investigated {len(per_invoice)} invoice(s).",
            "draft_reply": combined_reply(per_invoice) if per_invoice else no_invoice_reply(),
        }
        # Log the whole case and the combined draft first, so it is always visible
        # in CloudWatch even if the database write below fails.
        logger.info("%s", _readable(case_id, per_invoice, overall))
        store(case, overall, per_invoice)
        if overall["draft_reply"]:
            _mark_outlook(case.get("message_id"), DRAFT_READY)
        handled.append({"case_id": case_id, "invoices": len(per_invoice), "round": rounds,
                        "escalated": any_escalated, "helpdesk": overall["helpdesk"]})

    return {"statusCode": 200, "body": {"handled": handled}}


def _readable(case_id: str, per_invoice: List[Dict[str, Any]], overall: Dict[str, Any]) -> str:
    """Render the whole case for the CloudWatch console."""
    lines = ["", f"===== INVESTIGATION: {case_id} " + "=" * 28,
             f"  invoices        {len(per_invoice)}",
             f"  round           {overall.get('round')} (ask limit {MAX_ASK_ROUNDS})",
             f"  helpdesk        {overall['helpdesk']}",
             f"  export control  {overall['itar_controlled']}",
             f"  escalated       {overall['escalated']}"]
    for item in per_invoice:
        r = item.get("result") or {}
        lines += ["", f"  --- {item['invoice_number']}{' [ESCALATED]' if item.get('escalated') else ''} ---",
                  f"    outcome       {r.get('outcome')}",
                  f"    root cause    {str(r.get('root_cause') or '')[:90]}",
                  f"    next action   {str(r.get('next_action') or '')[:90]}"]
    if overall.get("draft_reply"):
        lines += ["", "  --- COMBINED SUPPLIER DRAFT ---"] + [f"    {l}" for l in overall["draft_reply"].splitlines()]
    lines.append("=" * 60)
    return "\n".join(lines)
