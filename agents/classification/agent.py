"""PSC classification agent business contract; runtime packaging/invocation pending approved AgentCore configuration."""
import json

def handle(payload:dict)->dict:
    if not isinstance(payload,dict) or not payload.get("case_id"):
        raise ValueError("case_id is mandatory")
    return {"case_id":payload["case_id"],"agent":"classification","status":"REQUIRES_CONFIGURED_MODEL_AND_GATEWAY"}
