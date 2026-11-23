
import json
import os
import logging
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sfn = boto3.client("stepfunctions")

SF2_ARN = os.environ["SF2_STATE_MACHINE_ARN"]


def lambda_handler(event, context):
    failures = []

    for record in event.get("Records", []):
        message_id = record["messageId"]

        try:
            body = json.loads(record["body"])

            if not isinstance(body, dict):
                raise ValueError(
                    "SQS1 message body must be a JSON object"
                )

            if not body.get("case_id"):
                raise ValueError("Missing case_id")

            if not body.get("message_id"):
                raise ValueError("Missing message_id")

            response = sfn.start_execution(
                stateMachineArn=SF2_ARN,
                input=json.dumps(body)
            )

            logger.info(
                "Started SF2 execution for case_id=%s executionArn=%s",
                body["case_id"],
                response["executionArn"]
            )

        except Exception:
            logger.exception(
                "Failed to dispatch SQS message %s",
                message_id
            )

            failures.append({
                "itemIdentifier": message_id
            })

    return {
        "batchItemFailures": failures
    }
