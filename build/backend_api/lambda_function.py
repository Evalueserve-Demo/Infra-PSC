import os
import json
import base64
import logging

import boto3
from botocore.exceptions import ClientError


logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

BUCKET = os.environ["OUTPUT_BUCKET"]
PREFIX = os.environ["OUTPUT_PREFIX"].rstrip("/") + "/"


def response(status_code, data):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Cache-Control": "no-store"
        },
        "body": json.dumps(data, default=str)
    }


def encode_key(key):
    return base64.urlsafe_b64encode(
        key.encode("utf-8")
    ).decode("utf-8").rstrip("=")


def decode_key(value):
    padding = "=" * (-len(value) % 4)

    return base64.b64decode(
        value + padding,
        altchars=b"-_",
        validate=True
    ).decode("utf-8")


def read_json(key):
    result = s3.get_object(
        Bucket=BUCKET,
        Key=key
    )

    with result["Body"] as body:
        return json.loads(body.read())


def list_cases(event):
    query = event.get("queryStringParameters") or {}

    try:
        limit = int(query.get("limit", 20))
    except (ValueError, TypeError):
        return response(400, {
            "error": "Invalid limit"
        })

    limit = max(1, min(limit, 50))

    params = {
        "Bucket": BUCKET,
        "Prefix": PREFIX,
        "MaxKeys": limit
    }

    if query.get("next_token"):
        params["ContinuationToken"] = query["next_token"]

    result = s3.list_objects_v2(**params)

    cases = []

    for item in result.get("Contents", []):
        key = item["Key"]

        if not key.endswith(".json"):
            continue

        data = read_json(key)

        classification = data.get("classification") or {}
        identifiers = data.get("identifiers") or {}

        cases.append({
            "id": encode_key(key),
            "case_id": data.get("case_id"),
            "message_id": data.get("message_id"),
            "subject": data.get("subject"),
            "invoice_number": identifiers.get("invoice_number"),
            "po_number": identifiers.get("po_number"),
            "request_type": classification.get("request_type"),
            "is_payment_related": classification.get(
                "is_payment_related"
            ),
            "confidence": classification.get("confidence"),
            "guardrail_status": data.get("guardrail_status"),
            "identifier_valid": identifiers.get("identifier_valid"),
            "investigate": data.get("investigate"),
            "processing_status": data.get("processing_status"),
            "exported_at": item["LastModified"].isoformat()
        })

    return response(200, {
        "count": len(cases),
        "cases": cases,
        "next_token": result.get("NextContinuationToken")
    })


def get_case(case_key):
    try:
        key = decode_key(case_key)
    except (ValueError, UnicodeError):
        return response(400, {
            "error": "Invalid case identifier"
        })

    if not key.startswith(PREFIX) or not key.endswith(".json"):
        return response(400, {
            "error": "Invalid case identifier"
        })

    data = read_json(key)

    return response(200, data)


def lambda_handler(event, context):
    try:
        method = event.get("httpMethod", "GET")
        path = event.get("path", "/cases").rstrip("/")

        if method != "GET":
            return response(405, {
                "error": "Method not allowed"
            })

        if path == "/cases":
            return list_cases(event)

        if path.startswith("/cases/"):
            case_key = path.split("/", 2)[2]
            return get_case(case_key)

        return response(404, {
            "error": "Endpoint not found"
        })

    except ClientError as error:
        code = error.response["Error"]["Code"]

        logger.error("AWS request failed: %s", code)

        if code in ("NoSuchKey", "NotFound", "404"):
            return response(404, {
                "error": "Case not found"
            })

        if code == "InvalidArgument":
            return response(400, {
                "error": "Invalid request"
            })

        return response(500, {
            "error": "Unable to retrieve processing output"
        })

    except Exception:
        logger.exception("Backend API processing failed")

        return response(500, {
            "error": "Internal server error"
        })