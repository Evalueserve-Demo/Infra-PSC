import json, os, logging, boto3
logger=logging.getLogger(__name__); logger.setLevel(logging.INFO)
def respond(status, **extra): return {"status":status, **extra}
def event_payload(event):
    if isinstance(event,dict) and "Records" in event:
        return [json.loads(r.get("body","{}")) for r in event["Records"]]
    return event

def lambda_handler(event, context):
    if not os.getenv("PR_ITAR_API_URL"): raise RuntimeError("PR/ITAR endpoint not configured")
    raise NotImplementedError("PR/ITAR request schema and auth contract pending")
