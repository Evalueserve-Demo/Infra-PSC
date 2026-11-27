
import os
import json
import logging
from datetime import datetime, timezone

import boto3


logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

OUTPUT_BUCKET = os.environ["OUTPUT_BUCKET"]
OUTPUT_PREFIX = os.environ.get(
    "OUTPUT_PREFIX", "sqs2-output"
).strip("/")


def lambda_handler(event, context):

    batch_item_failures = []

    for record in event["Records"]:

        message_id = record["messageId"]

        try:
            payload = json.loads(record["body"])

            sent_timestamp = int(
                record["attributes"]["SentTimestamp"]
            )

            message_date = datetime.fromtimestamp(
                sent_timestamp / 1000,
                tz=timezone.utc
            )

            date_prefix = message_date.strftime("%Y/%m/%d")

            object_key = (
                f"{OUTPUT_PREFIX}/"
                f"{date_prefix}/"
                f"{message_id}.json"
            )

            s3.put_object(
                Bucket=OUTPUT_BUCKET,
                Key=object_key,
                Body=json.dumps(
                    payload,
                    indent=2,
                    ensure_ascii=False
                ).encode("utf-8"),
                ContentType="application/json",
                IfNoneMatch="*"
            )

            logger.info(
                "EXPORT_SUCCESS message_id=%s s3_key=%s",
                message_id,
                object_key
            )

        except s3.exceptions.ClientError as error:

            error_code = error.response.get(
                "Error", {}
            ).get("Code")

            if error_code in ("PreconditionFailed", "412"):
                logger.info(
                    "EXPORT_ALREADY_EXISTS message_id=%s",
                    message_id
                )
                continue

            logger.error(
                "EXPORT_FAILED message_id=%s error_code=%s",
                message_id,
                error_code
            )

            batch_item_failures.append({
                "itemIdentifier": message_id
            })

        except Exception as error:

            logger.error(
                "EXPORT_FAILED message_id=%s error_type=%s",
                message_id,
                type(error).__name__
            )

            batch_item_failures.append({
                "itemIdentifier": message_id
            })

    return {
        "batchItemFailures": batch_item_failures
    }
