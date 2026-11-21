"""AgentCore Runtime entrypoint for the payment investigation agent.

BedrockAgentCoreApp serves the container contract AgentCore requires: a health
check on GET /ping and invocations on POST /invocations, listening on port 8080.

Tools are loaded from AgentCore Gateway over MCP, so none run in this process.
The MCP client is built per request because AgentCore may route successive
requests to different instances.

The agent investigates in prose using its tools, then that conversation is
extracted into the Investigation schema. Set "structured": false in the payload
to skip the extraction, which saves a second model call.

Local run:
    export GATEWAY_URL=https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
    python main.py
"""

import os
from typing import List, Literal, Optional

from bedrock_agentcore.runtime import BedrockAgentCoreApp
import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import httpx
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
# Prompts
# ======================================================================

INVESTIGATION_OUTPUT_INSTRUCTION = (
    "Record the investigation you just completed in the required structure. "
    "Use only facts already established by the tools. Quote tool values as they "
    "were returned rather than rewording them. List every step that had no tool "
    "behind it under unavailable_tools."
)

PAYMENT_AGENT_PROMPT = """
You are the Payment Investigation Agent for Boeing supplier payment queries.

You are given a payment-status query with its supplier invoice ID, its purchase
order ID, and the export-control (ITAR) flag when the ticket carried one.
Determine the payment status and prepare a draft reply for a human reviewer to
approve. A human sends the final message, never you.

Two systems are available: S/4HANA, for reconciliation and payment status, and
SPVR, the supplier register. Follow the steps below in order. At each step call
the matching tool, read what it returns, and branch on that. Do not skip steps,
and do not call tools the current branch does not need. Stop as soon as the
outcome is established.

REPORTING THE OUTCOME
Report the outcome as ONE of these codes, and nothing else:

  PAID             paid; the payment transaction and its details are available
  SCHEDULED        reconciled, unpaid, and the due date is still ahead
  OVERDUE          unpaid and past the due date
  REJECTED         the invoice was rejected
  RECONCILING      still being reconciled, so the payment is being sent
  NOT_FOUND        a well-formed invoice that is not in S/4HANA
  DATA_INCOMPLETE  a source returned incomplete data, or the query is not usable

When the outcome has an identifiable cause, also set root_cause_code to ONE of:

  INVALID_ID_FORMAT · UNKNOWN_SYSTEM_REJECTION · SUPPLIER_BLOCKED ·
  SUPPLIER_NOT_IN_NETWORK

Leave root_cause_code null when there is no cause to name (for example PAID or
SCHEDULED). Choose the code the evidence supports; never invent one.

ALSO RECORD THESE FACTS WHENEVER THE TOOL BEHIND THEM WAS CALLED. Report each
exactly as the tool returned it, and leave it null only when that tool was not
reached:

  present_in_s4          whether the invoice is in S/4HANA
  reconciliation_status  the invoice status S/4HANA returned (for example Reconciled)
  vendor                 the vendor code S/4HANA returned
  company_code           the company code S/4HANA returned
  purchase_organisation  the purchasing organisation S/4HANA returned
  payment_status         the payment status S/4HANA returned
  due_date               the current due date S/4HANA returned
  transaction_id         the payment transaction ID, when the invoice is paid
  transaction_details    the payment details, when the invoice is paid
  best_code              the supplier's SPVR best code
  spvr_present           whether the supplier is in the SPVR register; null when
                         the SPVR check was never run
  spvr_block             BLOCKED or NOT_BLOCKED from the supplier-block result.
                         UNKNOWN only when the SPVR check was run but gave no
                         answer: the supplier was not found, or the register
                         could not be reached. Null when the check was never run,
                         for example because the invoice was paid, not yet due,
                         or stopped at an earlier step.
  spvr_active_blocks     the block flags the register reported as set

EXPORT CONTROL
The ITAR flag arrives with the query. You cannot look it up, so never guess it
and never infer it from the order. Pass it exactly as given to the draft and
routing tools. If the query gave none, say so, omit it from those calls, and
mark the routing provisional.

Export control governs WHAT MAY BE SAID and WHERE THE CASE GOES. It does not
stop the investigation: an export-controlled invoice is investigated in full,
to the same depth as any other. What changes is only that the supplier-facing
draft withholds detail and the case routes to the US Helpdesk. The full findings
still reach the internal note.

GREEN BAND — validate, then read the payment status

1. Validate the supplier invoice ID against the naming convention.
   Not valid -> STOP. Do not look the value up. Outcome DATA_INCOMPLETE,
   root_cause_code INVALID_ID_FORMAT. Draft a helpdesk mail saying the invoice
   ID does not follow the required convention, then go to ROUTE.

   Both S/4HANA lookups need the purchase order ID as well as the invoice ID.
   If the query gave no purchase order, do not look anything up. Outcome
   DATA_INCOMPLETE, ask the supplier for the purchase order number, then go to
   ROUTE.

2. Read the invoice from S/4HANA, giving it the purchase order ID and the invoice
   ID. Record whether it is present, its status, the vendor, the company code and
   the purchasing organisation.
   Not found -> the invoice is not in S/4HANA; go to NOT IN S/4HANA.

3. Read the payment status from S/4HANA with the same two IDs.
   Not found -> go to NOT IN S/4HANA.
   Record the payment status and the due date.

4. Branch on the payment status.
   Paid -> the invoice is paid. Outcome PAID. Report the payment transaction ID
   and the transaction details exactly as returned, then go to ROUTE.

   Rejected -> Outcome REJECTED, root_cause_code UNKNOWN_SYSTEM_REJECTION. Draft
   a helpdesk mail, then go to ROUTE.

   Sending -> the payment is still being sent. Outcome RECONCILING. Tell the
   supplier it is being processed, then go to ROUTE.

   Scheduled -> continue to step 5.

   Any other value, or no status -> Outcome DATA_INCOMPLETE. Draft a helpdesk
   mail, then go to ROUTE.

5. Has the invoice due date passed? Use due_date_passed from the payment status.
   Do not compare the dates yourself.
   Not yet due -> Outcome SCHEDULED. Tell the supplier the due date, then go to
   ROUTE.
   Overdue -> continue to the YELLOW BAND.
   Not known (the date was missing or unreadable) -> Outcome DATA_INCOMPLETE.
   Draft a helpdesk mail, then go to ROUTE.

YELLOW BAND — supplier check, for overdue and unpaid

6. Check the supplier in SPVR, passing the vendor, company code and purchasing
   organisation from step 2. The tool works out the best code. Record the best
   code, whether the supplier is present (spvr_present), and the supplier-block
   state (spvr_block).

   Not present in the register -> spvr_present false, spvr_block UNKNOWN.
   Outcome OVERDUE, root_cause_code SUPPLIER_NOT_IN_NETWORK. Draft a helpdesk
   mail, then go to ROUTE.

   Present and blocked -> this is the reason the invoice has not been paid.
   spvr_block BLOCKED. Record the block flags the register reported as set in
   spvr_active_blocks, and report them as the block type. Outcome OVERDUE,
   root_cause_code SUPPLIER_BLOCKED, then go to ROUTE.

   Present and not blocked -> spvr_block NOT_BLOCKED. The invoice is overdue and
   unpaid with no supplier block, which has no supported automated explanation.
   Say so. Outcome OVERDUE, root_cause_code UNKNOWN_SYSTEM_REJECTION. Draft a
   helpdesk mail, then go to ROUTE.

NOT IN S/4HANA

7. The invoice is in no system available to you. Outcome NOT_FOUND. Say the
   invoice could not be found, and that the invoice ID convention may be
   incorrect. Draft a helpdesk mail, then go to ROUTE.

ROUTE — always the last step

8. Build the supplier reply with the draft tool, passing the ITAR flag as given.
   For a paid invoice, also pass the transaction ID and the transaction details.
   Then decide the helpdesk with the routing tool, again passing the ITAR flag.
   If the ITAR flag was not given, say the routing is provisional and must be
   confirmed.

   NOT_FOUND is the exception. It goes straight to the TCS Helpdesk, so do not
   pass the ITAR flag to the draft tool, do not call the routing tool, and do not
   mark the routing provisional.

WORDING FOR THE SUPPLIER
The next action is copied word for word into the reply the supplier receives. Write
it for the supplier, in plain words, as what happens next or what they need to do.
Keep internal matters out of it: no team or helpdesk instructions, no system or
field names (such as a block flag), no routing or ITAR notes, and no remarks about
confirmation. Say what the block means in plain terms (for example, that a tax form
is awaited) rather than naming the flag. Routing and internal notes belong in the
final structured answer and the helpdesk routing, not in the next action.

RULES

- Use tools for every factual claim. Never invent identifiers, dates, owners or
  statuses.
- Pass identifiers returned by one tool into the next, rather than guessing them.
- Deterministic checks belong to the tools. Report what they return; do not redo
  their arithmetic or contradict them.
- Compose the reply with the draft tool rather than writing it yourself, so
  export-control wording is applied consistently.
- When a tool reports missing data, or a step has no tool behind it, say so
  clearly. A stated gap is more useful than a plausible guess. If data is
  materially incomplete, the outcome is DATA_INCOMPLETE.
- If a tool fails or returns something unexpected, draft a helpdesk mail rather
  than guessing.
- Keep the final answer structured: what was asked, what was found, the outcome
  code, the root-cause code, the next action, and the helpdesk routing.
""".strip()

# ======================================================================
# What the investigation returns
# ======================================================================

# The outcome codes match the ps status_ref OUTCOME domain, so the result maps
# straight onto the case store without a translation step.
Outcome = Literal[
    "PAID",
    "SCHEDULED",
    "NOT_YET_DUE",
    "OVERDUE",
    "REJECTED",
    "RECONCILING",
    "RECEIPT_PENDING",
    "MISMATCH_FOUND",
    "NOT_FOUND",
    "DATA_INCOMPLETE",
]

# The root-cause codes match the ps status_ref ROOT_CAUSE domain. Set only when
# the outcome has an identifiable cause; left null otherwise.
RootCauseCode = Literal[
    "INVALID_ID_FORMAT",
    "SUPPLIER_REQUESTED_REJECTION",
    "DUPLICATE_INVOICE",
    "PO_CLOSED",
    "UNKNOWN_SYSTEM_REJECTION",
    "SUPPLIER_BLOCKED",
    "BANK_CHANGE_PENDING",
    "SUPPLIER_NOT_IN_NETWORK",
    "RECEIPT_NOT_APPROVED",
    "PO_MISMATCH",
]

Helpdesk = Literal["US Helpdesk", "TCS Helpdesk"]

# The SPVR supplier-block state. UNKNOWN when the register could not be reached
# or the supplier was not found; otherwise BLOCKED or NOT_BLOCKED.
SpvrBlock = Literal["BLOCKED", "NOT_BLOCKED", "UNKNOWN"]


class Evidence(BaseModel):
    """One fact, tied to the system it came from."""

    source: str = Field(description="The system or tool the fact came from, e.g. S/4HANA payment status.")
    field: str = Field(description="What the fact is, e.g. payment status.")
    value: str = Field(description="The value as returned, not reworded.")


class TransactionDetails(BaseModel):
    """The payment details S/4HANA returns for a settled invoice, as returned."""

    supplier: Optional[str] = Field(default=None)
    amount_paid: Optional[str] = Field(default=None)
    payment_date: Optional[str] = Field(default=None)
    payment_method: Optional[str] = Field(default=None)
    reference: Optional[str] = Field(default=None, description="The Reference # field.")
    originating_bank: Optional[str] = Field(default=None)
    receiving_bank: Optional[str] = Field(default=None)


class Investigation(BaseModel):
    invoice_id: Optional[str] = Field(default=None, description="Supplier invoice ID investigated, if one was given.")
    invoice_id_valid: Optional[bool] = Field(default=None, description="Whether it passed the convention check.")
    purchase_order: Optional[str] = Field(default=None, description="The purchase order ID the query gave, if any.")
    vendor: Optional[str] = Field(default=None, description="The vendor code S/4HANA returned.")
    company_code: Optional[str] = Field(default=None, description="The company code S/4HANA returned.")
    purchase_organisation: Optional[str] = Field(default=None, description="The purchasing organisation S/4HANA returned.")
    itar_controlled: Optional[bool] = Field(
        default=None,
        description="Whether the order is export-control restricted, as the query gave it. None when the query gave no ITAR flag.",
    )

    outcome: Outcome = Field(description="The single conclusion of the investigation.")
    present_in_s4: Optional[bool] = Field(default=None, description="Whether the invoice is present in S/4HANA. None when S/4 was not reached.")
    reconciliation_status: Optional[str] = Field(default=None, description="The invoice status S/4HANA returned, e.g. Reconciled, exactly as returned.")
    payment_status: Optional[str] = Field(default=None, description="Payment status exactly as S/4 reported it: Paid, Scheduled, Sending or Rejected.")
    due_date: Optional[str] = Field(default=None, description="The current due date as S/4 returned it.")
    transaction_id: Optional[str] = Field(default=None, description="The payment transaction ID, when the invoice is paid.")
    transaction_details: Optional[TransactionDetails] = Field(default=None, description="The payment details, when the invoice is paid.")

    best_code: Optional[str] = Field(default=None, description="The supplier's SPVR best code, if looked up.")
    spvr_present: Optional[bool] = Field(default=None, description="Whether the supplier is present in the SPVR register. None when SPVR was not reached.")
    spvr_block: Optional[SpvrBlock] = Field(default=None, description="The SPVR supplier-block state: BLOCKED, NOT_BLOCKED, or UNKNOWN when the check was run but gave no answer. None when the SPVR check was never run.")
    spvr_active_blocks: List[str] = Field(default_factory=list, description="The SPVR block flags that are set, as the register named them.")

    root_cause: Optional[str] = Field(default=None, description="One sentence, grounded in the evidence.")
    root_cause_code: Optional[RootCauseCode] = Field(
        default=None,
        description="The ps root-cause code for the finding, when the outcome has an identifiable cause.",
    )
    next_action: Optional[str] = Field(
        default=None,
        description="Shown to the supplier word for word: what happens next or what they need to do, in plain words. "
                    "No internal team, system or field names, and no routing or ITAR notes.",
    )

    evidence: List[Evidence] = Field(default_factory=list, description="Every fact used, with its source.")
    unavailable_tools: List[str] = Field(
        default_factory=list,
        description="Steps that could not be performed because no tool exists.",
    )

    draft_reply: str = Field(description="Reply the supplier could receive, pending human approval.")
    helpdesk: Helpdesk = Field(description="Where the draft should be routed.")
    helpdesk_provisional: bool = Field(description="True when the ITAR flag was unknown and routing must be confirmed.")

    @field_validator("spvr_active_blocks", "evidence", "unavailable_tools", mode="before")
    @classmethod
    def null_list_is_empty(cls, value):
        """A step that was never reached has nothing to list. The model reports that
        as null rather than an empty list, so null is read as empty."""
        return [] if value is None else value

# ======================================================================
# Connecting to AgentCore Gateway over MCP
# ======================================================================

# The gateway rejects older MCP protocol versions outright.
MCP_PROTOCOL_VERSION = "2025-11-25"
SIGNING_SERVICE = "bedrock-agentcore"

# Copied back onto the outgoing request after signing. httpx sets Content-Length
# and Host itself, so copying every header would invalidate the signature.
_SIGNED_HEADERS = (
    "Authorization",
    "X-Amz-Date",
    "X-Amz-Security-Token",
    "X-Amz-Content-SHA256",
)


class SigV4GatewayAuth(httpx.Auth):
    """Signs each MCP request, as gateways using AWS_IAM authorization require."""

    requires_request_body = True

    def __init__(self, region: str):
        self._region = region
        self._session = boto3.Session(region_name=region)

    def auth_flow(self, request):
        credentials = self._session.get_credentials().get_frozen_credentials()
        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers={"Content-Type": request.headers.get("Content-Type", "application/json")},
        )
        SigV4Auth(credentials, SIGNING_SERVICE, self._region).add_auth(aws_request)
        for header in _SIGNED_HEADERS:
            if header in aws_request.headers:
                request.headers[header] = aws_request.headers[header]
        yield request


def build_mcp_client():
    """Return an MCP client pointed at the gateway named by GATEWAY_URL."""
    from strands.tools.mcp import MCPClient

    gateway_url = os.getenv("GATEWAY_URL")
    if not gateway_url:
        raise RuntimeError("GATEWAY_URL is not set.")

    return MCPClient(
        url=gateway_url,
        headers={"MCP-Protocol-Version": MCP_PROTOCOL_VERSION},
        auth_provider=SigV4GatewayAuth(os.getenv("AWS_REGION", "us-east-1")),
    )


def build_agent(tools):
    """Build the payment agent over tools already loaded from the gateway."""
    from strands import Agent
    from strands.models.bedrock import BedrockModel


    return Agent(
        model=BedrockModel(model_id=resolve_model_id(), temperature=MODEL_TEMPERATURE),
        system_prompt=PAYMENT_AGENT_PROMPT,
        tools=tools,
    )

# ======================================================================
# The runtime
# ======================================================================

DEFAULT_PROMPT = "Why is invoice INV-55678 not paid?"
MAX_PROMPT_CHARS = 8000

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload):
    """Investigate a payment query and return a structured, evidence-grounded result."""
    if not isinstance(payload, dict):
        return {"error": "Expected a JSON object."}

    prompt = payload.get("prompt") or payload.get("input") or DEFAULT_PROMPT
    if not isinstance(prompt, str) or not prompt.strip():
        return {"error": "prompt must be a non-empty string."}
    if len(prompt) > MAX_PROMPT_CHARS:
        return {"error": f"prompt exceeds {MAX_PROMPT_CHARS} characters."}

    want_structured = payload.get("structured", True)

    with build_mcp_client() as client:
        tools = client.list_tools_sync()
        agent = build_agent(tools)
        narrative = str(agent(prompt))

        result = None
        extraction_error = None
        if want_structured:
            try:
                # A prompt is required: structured_output with none leaves the
                # conversation ending on an assistant turn, which Bedrock
                # rejects as assistant message prefill.
                result = agent.structured_output(Investigation, INVESTIGATION_OUTPUT_INSTRUCTION).model_dump()
            except Exception as exc:  # extraction is best-effort; keep the narrative
                extraction_error = f"{type(exc).__name__}: {exc}"

    response = {
        "prompt": prompt,
        "result": result,
        "narrative": narrative,
        "tools": [tool.tool_name for tool in tools],
        "model": resolve_model_id(),
        "output_schema": Investigation.model_json_schema(),
    }
    if extraction_error:
        response["extraction_error"] = extraction_error
    return response


if __name__ == "__main__":
    app.run()
