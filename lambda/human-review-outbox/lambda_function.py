
import json
import logging
import os

import boto3
import pg8000.dbapi

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

rds = boto3.client("rds")
sqs = boto3.client("sqs")


def connect_db():
    host = os.environ["DB_PROXY_ENDPOINT"]

    token = rds.generate_db_auth_token(
        DBHostname=host,
        Port=5432,
        DBUsername=os.environ["DB_USER"],
        Region=os.environ["AWS_REGION"],
    )

    return pg8000.dbapi.connect(
        host=host,
        port=5432,
        database=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=token,
        ssl_context=True,
        timeout=10,
    )


def lambda_handler(event, context):
    published = 0

    batch_size = int(
        os.environ.get("OUTBOX_BATCH_SIZE", "10")
    )

    max_attempts = int(
        os.environ.get("OUTBOX_MAX_ATTEMPTS", "20")
    )

    for _ in range(batch_size):
        connection = connect_db()

        try:
            cursor = connection.cursor()

            cursor.execute(
                """
                SELECT
                    event_id,
                    event_payload
                FROM human_review_outbox
                WHERE publish_status IN ('PENDING', 'FAILED')
                  AND attempts < %s
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """,
                (max_attempts,),
            )

            record = cursor.fetchone()

            if not record:
                connection.rollback()
                break

            event_id, payload = record

            if isinstance(payload, str):
                payload = json.loads(payload)

            try:
                sqs.send_message(
                    QueueUrl=os.environ["REPLY_QUEUE_URL"],
                    MessageBody=json.dumps(payload),
                )

            except Exception as error:
                cursor.execute(
                    """
                    UPDATE human_review_outbox
                    SET
                        publish_status = 'FAILED',
                        attempts = attempts + 1,
                        last_error = %s
                    WHERE event_id = %s
                    """,
                    (type(error).__name__, event_id),
                )

                connection.commit()

                logger.exception(
                    "Outbox publish failed: event=%s",
                    event_id,
                )

                break

            cursor.execute(
                """
                UPDATE human_review_outbox
                SET
                    publish_status = 'PUBLISHED',
                    attempts = attempts + 1,
                    published_at = CURRENT_TIMESTAMP,
                    last_error = NULL
                WHERE event_id = %s
                """,
                (event_id,),
            )

            connection.commit()
            published += 1

        except Exception:
            connection.rollback()
            logger.exception(
                "Outbox transaction failed"
            )
            raise

        finally:
            connection.close()

    return {
        "published": published
    }
