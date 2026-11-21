"""AgentCore Runtime entry point for the intake agents.

Classification (Stage 5) and extraction (Stage 6) are deployed as separate
runtimes from this one package, because the flow document invokes them at
different points and each should be able to fail without the other. AGENT_ROLE
selects which one a given runtime serves.

Both agents have no tools, so neither needs a Gateway connection. They are the
model, a prompt, and a schema.

Environment:
    AGENT_ROLE          "classification" or "extraction"
    BEDROCK_MODEL_ID    overrides the model every agent runs on, intake included
    INTAKE_MODEL_ID     pins the intake model alone, when it must differ
"""

import os
from typing import Any, Dict, List, Literal, Optional

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from pydantic import BaseModel, Field, field_validator

# ======================================================================
# Which Bedrock model the agents run on
# ======================================================================

# An inference profile, not a bare model ID: on-demand throughput is not
# supported without one. Every agent — classification, extraction and
# investigation — runs on this one model.
DEFAULT_MODEL_ID = "global.anthropic.claude-sonnet-4-6"

# Classification and extraction are rule-following work, and demo output that
# should not drift between runs.
MODEL_TEMPERATURE = 0.0


def resolve_model_id() -> str:
    return os.getenv("BEDROCK_MODEL_ID") or DEFAULT_MODEL_ID


def resolve_intake_model_id() -> str:
    """The model for the classification and extraction agents (Stages 5 and 6).

    The same model as investigation. Overridable with INTAKE_MODEL_ID or
    BEDROCK_MODEL_ID when one needs to be pinned separately.
    """
    return os.getenv("INTAKE_MODEL_ID") or resolve_model_id()

# ======================================================================
# How an email, and the text of its attachments, is laid out for the agents
# ======================================================================

def _attachment_label(attachment: dict) -> str:
    return attachment.get("filename") or attachment.get("name") or "attachment"


def classification_input(subject: str, body: str, sender: str = "",
                         attachments: Optional[List[dict]] = None) -> str:
    """The email for the classifier, with a short excerpt of each attachment.

    The classifier only decides whether the email is ours, so it sees enough of
    an attachment to recognise an invoice, not the whole document.
    """
    text = f"From: {sender}\nSubject: {subject}\n\n{body}"
    if attachments:
        lines = ["", "", "ATTACHMENTS:"]
        for attachment in attachments:
            excerpt = attachment.get("excerpt")
            lines.append(f"- {_attachment_label(attachment)}" + (f":\n{excerpt}" if excerpt else ""))
        text += "\n".join(lines)
    return text


def extraction_input(subject: str, body: str, attachments: Optional[List[dict]] = None) -> str:
    """The email and the full text of each attachment, for the extractor."""
    parts = [f"EMAIL SUBJECT:\n{subject}", f"\nEMAIL BODY:\n{body}"]
    for attachment in attachments or []:
        text = attachment.get("extracted_text")
        if not text:
            continue
        parts.append(f"\nATTACHMENT ({_attachment_label(attachment)}):\n{text}")
    return "\n".join(parts)

# ======================================================================
# Prompts
# ======================================================================

CLASSIFICATION_PROMPT = """
You are the Payment Mail Classification Agent for Boeing's supplier payment
desk.

Your only job is to read one supplier email and sort it. You do not extract
identifiers, you do not investigate, and you have no tools. You analyse the
text and nothing else.

Decide:

1. is_payment_related — true only if the email concerns a payment, invoice,
   purchase order, remittance or payment status. Otherwise false.

2. request_type — what the supplier is asking for:
   PAYMENT_STATUS_REQUEST     asks where a payment or invoice stands: whether
                              it is received, reconciled, scheduled, paid or
                              held, why it has not been paid, when it will be,
                              or a payment marked paid that has not arrived
                              (including asking for its payment date or
                              reference)
   INVOICE_DISPUTE            the supplier disputes an amount or a deduction
   REMITTANCE_ADVICE_REQUEST  the supplier asks for the remittance advice or
                              breakdown of a payment already made
   PO_INVOICE_MISMATCH        the supplier is reporting a mismatch between
                              their invoice and the purchase order
   OTHER_PAYMENT_QUERY        payment related but none of the above, such as
                              submitting an invoice or credit note, correcting
                              one, or changing supplier details
   NOT_PAYMENT_RELATED        not for this desk

3. query_summary — one sentence describing what the supplier is actually
   asking. Do not include purchase order numbers or part detail; this text is
   carried forward and may reach an export-controlled reply.

4. missing_information — anything a payments analyst would still need that the
   email does not give, such as "no invoice number provided". May be empty.

5. confidence — how certain you are of request_type, on a scale of 1 to 10
   where 1 is the lowest and 10 the highest. Use 8 to 10 when the email is
   clearly one type, 4 to 7 when it is probably that type but could be read
   another way, and 1 to 3 when it is genuinely ambiguous. A score below 8
   means a human should look at it.

6. mentions_account_change — true if the email asks for, or merely mentions, a
   change to the supplier's bank account, payment or remittance details, even
   when it also asks something else, such as a payment status question.
   Otherwise false.

Judge only what the email says. Never infer an invoice number, an amount or an
intent that is not written down.
""".strip()

CLASSIFICATION_OUTPUT_INSTRUCTION = "Record that classification in the required structure."

EXTRACTION_PROMPT = """
You are the Payment Identifier Extraction Agent for Boeing's supplier payment
desk.

You are given a supplier email and, when present, the text of its attachments.
Pull out every purchase order and invoice identifier you can find. Read the
attachment text as carefully as the email body: the real numbers are often only
on the attached invoice.

What they look like:
- A purchase order is a 10-digit number, for example 4500123456.
- An invoice is INV- followed by digits, for example INV-55678.

Rules:
- Pair a purchase order with its invoice when the text makes the link clear.
- When an invoice appears with no purchase order, return the pair with
  po_number null, and the other way round.
- Record where you found each pair: EMAIL_SUBJECT, EMAIL_BODY or ATTACHMENT.
- Never invent an identifier. If the email contains none, return an empty list.
  An empty list is a useful answer; a guessed invoice number is not.
- Return a value exactly as it is written. Do not correct, pad or reformat it.
  A malformed identifier is checked downstream, and silently fixing one here
  would hide a supplier's mistake.
""".strip()

EXTRACTION_OUTPUT_INSTRUCTION = "Record the identifiers you found in the required structure."

# ======================================================================
# Classification: what the agent returns
# ======================================================================

# The flow document names PAYMENT_STATUS_REQUEST as the supported classification
# and says an unsupported one must not continue to Stage 6. The rest of these
# exist so the agent can say which kind of unsupported it is, rather than
# everything that is not a status request looking identical downstream.
RequestType = Literal[
    "PAYMENT_STATUS_REQUEST",
    "INVOICE_DISPUTE",
    "REMITTANCE_ADVICE_REQUEST",
    "PO_INVOICE_MISMATCH",
    "OTHER_PAYMENT_QUERY",
    "NOT_PAYMENT_RELATED",
]

class Classification(BaseModel):
    is_payment_related: bool = Field(description="Whether this email belongs to the payment desk.")
    request_type: RequestType = Field(description="What the supplier is asking for.")
    query_summary: str = Field(
        description="One sentence on what the supplier wants. No purchase order numbers or part detail."
    )
    missing_information: List[str] = Field(
        default_factory=list, description="What a payments analyst would still need."
    )
    mentions_account_change: bool = Field(
        default=False,
        description="True if the email asks for or mentions a change to bank, payment or remittance details, "
                    "even alongside another request.",
    )
    confidence: int = Field(
        ge=1, le=10,
        description="Certainty of request_type on a 1 to 10 scale, 1 lowest and 10 highest. "
                    "Scores below 8 mean the email is ambiguous enough that a human should look.",
    )

    @field_validator("missing_information", mode="before")
    @classmethod
    def normalize_missing_information(cls, value):
        # The model sometimes returns null or {} instead of a list; treat as empty.
        if value is None or value == {}:
            return []
        return value

# ======================================================================
# Extraction: what the agent returns
# ======================================================================

class IdentifierPair(BaseModel):
    po_number: Optional[str] = Field(default=None, description="10-digit purchase order, or null.")
    invoice_number: Optional[str] = Field(default=None, description="Invoice identifier, or null.")
    found_in: Optional[str] = Field(
        default=None, description="EMAIL_SUBJECT, EMAIL_BODY or ATTACHMENT."
    )


class Extraction(BaseModel):
    payment_info: List[IdentifierPair] = Field(
        default_factory=list, description="Every identifier pair found. May be empty."
    )

# ======================================================================
# An email stopped before it could be classified
# ======================================================================

def withheld(reason: str) -> dict:
    """The classification for an email that was stopped before it could be classified."""
    return {"is_payment_related": False, "request_type": "BLOCKED_BY_GUARDRAIL", "confidence": 10,
            "query_summary": f"Withheld: {reason}."}

# ======================================================================
# The runtime
# ======================================================================

MAX_INPUT_CHARS = 20000

app = BedrockAgentCoreApp()


def _agent(system_prompt: str):
    """Build a tool-less Strands agent on the configured model."""
    from strands import Agent
    from strands.models.bedrock import BedrockModel

    return Agent(
        model=BedrockModel(model_id=resolve_intake_model_id(), temperature=MODEL_TEMPERATURE),
        system_prompt=system_prompt,
    )


def _answer(text: str, analysis: str, role: str, result, schema) -> Dict[str, Any]:
    """The structured answer, with what the model was given and said, for the Lambda's audit record."""
    return {role: result.model_dump(), "input": text[:MAX_INPUT_CHARS], "analysis": analysis,
            "output_schema": schema.model_json_schema()}


def classify(payload: Dict[str, Any]) -> Dict[str, Any]:
    text = payload.get("text") or classification_input(
        payload.get("subject", ""), payload.get("body", ""), payload.get("sender", "")
    )
    agent = _agent(CLASSIFICATION_PROMPT)
    try:
        analysis = str(agent(text[:MAX_INPUT_CHARS]))
        # structured_output needs a prompt of its own: ending on an assistant turn
        # is rejected by Bedrock as assistant message prefill.
        result = agent.structured_output(Classification, CLASSIFICATION_OUTPUT_INSTRUCTION)
    except ValueError as exc:
        # The model's own content filter stopped on the email; it is withheld like a guardrail block.
        if "content_filtered" not in str(exc):
            raise
        return {"classification": withheld("the model's content filter stopped it")}
    return _answer(text, analysis, "classification", result, Classification)


def extract(payload: Dict[str, Any]) -> Dict[str, Any]:
    text = payload.get("text") or extraction_input(
        payload.get("subject", ""), payload.get("body", ""), payload.get("attachments")
    )
    agent = _agent(EXTRACTION_PROMPT)
    analysis = str(agent(text[:MAX_INPUT_CHARS]))
    result = agent.structured_output(Extraction, EXTRACTION_OUTPUT_INSTRUCTION)
    return _answer(text, analysis, "extraction", result, Extraction)


ROLES = {"classification": classify, "extraction": extract}


@app.entrypoint
def invoke(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"error": "Expected a JSON object."}

    role = payload.get("role") or os.getenv("AGENT_ROLE", "classification")
    handler = ROLES.get(role)
    if handler is None:
        return {"error": f"Unknown role {role!r}. Expected one of {sorted(ROLES)}."}

    if not any(payload.get(k) for k in ("text", "subject", "body")):
        return {"error": "Provide text, or subject and body."}

    return {"role": role, "model_id": resolve_intake_model_id(), **handler(payload)}


if __name__ == "__main__":
    app.run()
