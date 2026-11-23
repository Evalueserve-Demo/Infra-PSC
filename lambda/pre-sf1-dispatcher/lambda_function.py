
"""
166 - PRE-SF1 DISPATCHER LAMBDA

Flow:
Email Ingestion -> Pre-SF1 SQS -> Dispatcher -> SF1

Responsibilities:
- Read SQS records.
- Validate and parse the JSON payload.
- Start SF1 with the original message object.
- Use deterministic execution names for idempotency.
- Return partial batch failures so unsuccessful records retry.
"""

import hashlib
import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sfn = boto3.client("stepfunctions")


def execution_name(message_id: str) -> str:
    """Stable, Step Functions-compatible name for a Graph message."""
    digest = hashlib.sha256(message_id.encode("utf-8")).hexdigest()
    return f"psc-ing-{digest[:56]}"


def dispatch(record: dict) -> None:
    body = record.get("body")

    if not isinstance(body, str):
        raise ValueError("SQS record body must be a string")

    payload = json.loads(body)

    if not isinstance(payload, dict):
        raise ValueError("SQS message body must contain a JSON object")

    message_id = payload.get("message_id")

    if not isinstance(message_id, str) or not message_id.strip():
        raise ValueError("SQS message must contain a non-empty message_id")

    sf1_arn = os.environ["SF1_ARN"]
    name = execution_name(message_id)

    try:
        response = sfn.start_execution(
            stateMachineArn=sf1_arn,
            name=name,
            input=json.dumps(
                payload,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        )

        logger.info(
            "Started SF1 execution for message_id=%s execution_arn=%s",
            message_id,
            response["executionArn"],
        )

    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")

        if error_code == "ExecutionAlreadyExists":
            # Standard workflows reject reuse of an execution name
            # after completion. Treat the same message_id as delivered.
            logger.info(
                "SF1 execution already exists for message_id=%s name=%s",
                message_id,
                name,
            )
            return

        logger.exception(
            "Failed to start SF1 for message_id=%s",
            message_id,
        )
        raise


def lambda_handler(event, context):
    failures = []

    for record in event.get("Records", []):
        sqs_message_id = record.get("messageId")

        try:
            dispatch(record)

        except Exception:
            logger.exception(
                "Pre-SF1 dispatch failed for SQS messageId=%s",
                sqs_message_id,
            )

            failures.append({
                "itemIdentifier": sqs_message_id
            })

    return {
        "batchItemFailures": failures
    }
