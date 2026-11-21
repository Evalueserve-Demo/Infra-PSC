"""What the pipeline concluded for each mail, and why.

Reads the ps case store, where the classification and the investigation result
are both recorded as case events. Shared by scripts/show_results.py and the
results Lambda, so what people see on the command line and over the endpoint
cannot differ.

A conversation can hold several mails. Each mail carries its own
classification; the investigation result is the latest one for the conversation.
A mail processed before classifications were recorded shows "not recorded".
"""

import csv
import io
import json
from typing import Any, Dict, List

NOT_RECORDED = "not recorded"
NOT_INVESTIGATED = "not investigated"
# The investigation ran but the mail named no usable invoice, so the draft asks
# the supplier for one.
NO_INVOICE = "no invoice number found; supplier asked for it"

LIST_SQL = """
select h.case_number, h.case_status, h.outcome_code, h.assigned_queue, h.is_itar,
       h.escalation_reason, m.received_at, m.sender_address, m.subject, m.body_text as body,
       cls.details as classification_event,
       inv.details as investigation_event,
       (select string_agg(i.supplied_value, ', ' order by i.created_at)
          from ps.case_identifier i where i.case_id = h.case_id) as identifiers,
       (select json_agg(json_build_object('type', i.identifier_type, 'value', i.supplied_value,
                                          'source', i.source, 'validation', i.validation_status)
                        order by i.created_at)
          from ps.case_identifier i where i.case_id = h.case_id) as identifier_rows
from ps.case_message m
join ps.case_header h on h.case_id = m.case_id
left join lateral (
    select e.details from ps.case_event e
    where e.case_id = m.case_id and e.event_type = 'CLASSIFICATION_RESULT'
      and e.details ->> 'graph_message_id' = m.graph_message_id
    order by e.occurred_at desc limit 1
) cls on true
left join lateral (
    select e.details from ps.case_event e
    where e.case_id = h.case_id and e.event_type = 'INVESTIGATION_RESULT'
    order by e.occurred_at desc limit 1
) inv on true
where m.direction = 'INBOUND'
order by m.received_at
"""

CASE_SQL = LIST_SQL.replace("where m.direction = 'INBOUND'",
                            "where m.direction = 'INBOUND' and h.case_number = %s")

SUMMARY_COLUMNS = ["received", "from", "subject", "case", "request_type", "confidence", "payment_related",
                   "label", "identifiers", "invoices", "status", "queue", "export_control", "next_action"]
# What the pipeline stored for the mail, as text. Large, so only the export and the local CSV carry it.
OUTPUT_COLUMNS = ["body", "classification_output", "extraction_output", "investigation_output", "draft_reply"]
COLUMNS = SUMMARY_COLUMNS + OUTPUT_COLUMNS
WITHHELD = "withheld: export-controlled"


def _per_invoice(investigation: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    return (investigation or {}).get("per_invoice") or []


def _body(row: Dict[str, Any]) -> str:
    return WITHHELD if row.get("is_itar") else row.get("body") or ""


def _outputs(row: Dict[str, Any]) -> Dict[str, str]:
    """What is stored for the mail: its text, the classification, the extracted identifiers, the investigation, the draft.

    The investigation holds the details the supplier draft leaves out for an export-controlled
    case, and the mail's own text can carry the same details, so both are withheld there. The
    draft is already redacted.
    """
    def text(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":")) if value else ""

    investigation = row.get("investigation_event") or {}
    return {
        "body": _body(row),
        "classification_output": text((row.get("classification_event") or {}).get("classification")),
        "extraction_output": text(row.get("identifier_rows")),
        "investigation_output": WITHHELD if row.get("is_itar") else text(investigation.get("per_invoice")),
        "draft_reply": investigation.get("draft_reply") or "",
    }


def summarise(row: Dict[str, Any], outputs: bool = False) -> Dict[str, Any]:
    """One mail, flattened to the fields worth reading across fifty of them.

    With outputs=True it also carries what the pipeline stored for the mail."""
    classification = (row.get("classification_event") or {}).get("classification") or {}
    label = (row.get("classification_event") or {}).get("mailbox_category")
    results = _per_invoice(row.get("investigation_event"))

    invoices = []
    outcomes = []
    next_actions = []
    for item in results:
        result = item.get("result") or {}
        outcome = result.get("outcome") or "UNKNOWN"
        cause = result.get("root_cause")
        marker = " [escalated]" if item.get("escalated") else ""
        outcomes.append(outcome)
        invoices.append(f"{item.get('invoice_number')}: {outcome}" + (f" / {cause}" if cause else "") + marker)
        if result.get("next_action"):
            next_actions.append(str(result["next_action"]))

    if results:
        investigated = "; ".join(invoices)
    elif row.get("investigation_event"):
        investigated = NO_INVOICE
    else:
        investigated = NOT_INVESTIGATED
    outcomes = outcomes or [investigated]

    recorded = bool(classification)
    summary = {
        "received": f"{row['received_at']:%Y-%m-%d %H:%M}",
        "from": row.get("sender_address") or "",
        "subject": row.get("subject") or "",
        "case": row["case_number"],
        "request_type": classification.get("request_type") or NOT_RECORDED,
        "confidence": classification.get("confidence") if recorded else "",
        "payment_related": classification.get("is_payment_related") if recorded else "",
        "label": label or "",
        "identifiers": row.get("identifiers") or "",
        "invoices": investigated,
        "outcomes": outcomes,
        "status": row.get("case_status") or "",
        "queue": row.get("assigned_queue") or "",
        "export_control": "yes" if row.get("is_itar") else "no",
        "next_action": " | ".join(next_actions),
    }
    return {**summary, **_outputs(row)} if outputs else summary


def fetch(database, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    with database.cursor() as cur:
        cur.execute(sql, params)
        names = [column.name for column in cur.description]
        return [dict(zip(names, record)) for record in cur.fetchall()]


def _inert(value: Any) -> Any:
    """Stop a cell that a sender wrote from running as a spreadsheet formula."""
    return "'" + value if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def to_csv(rows: List[Dict[str, Any]], columns: List[str] = SUMMARY_COLUMNS) -> str:
    """The rows as a spreadsheet. The byte-order mark lets Excel read the UTF-8."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows({column: _inert(value) for column, value in row.items()} for row in rows)
    return "﻿" + buffer.getvalue()


def case_detail(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Everything recorded for one mail: the summary, the findings and the draft."""
    classification_event = raw.get("classification_event") or {}
    investigation = raw.get("investigation_event") or {}
    return {
        "summary": summarise(raw),
        "body": _body(raw),
        "classification": classification_event.get("classification"),
        "outlook_label": classification_event.get("mailbox_category"),
        "guardrail": classification_event.get("guardrail"),
        "investigation": investigation.get("per_invoice"),
        "draft_reply": investigation.get("draft_reply"),
        "escalation_reason": raw.get("escalation_reason"),
    }
