import json, os, logging, boto3
logger=logging.getLogger(__name__); logger.setLevel(logging.INFO)
def respond(status, **extra): return {"status":status, **extra}
def event_payload(event):
    if isinstance(event,dict) and "Records" in event:
        return [json.loads(r.get("body","{}")) for r in event["Records"]]
    return event

def lambda_handler(event, context):
    if not os.getenv("ARIBA_API_URL"): raise RuntimeError("SAP Ariba endpoint not configured")
    raise NotImplementedError("SAP Ariba request schema and auth contract pending")
