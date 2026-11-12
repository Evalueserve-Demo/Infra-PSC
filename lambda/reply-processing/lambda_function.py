
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

import boto3
import pg8000.dbapi

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

rds = boto3.client("rds")
secrets = boto3.client("secretsmanager")


def db_connect():
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


def post_json(url, payload, headers=None, timeout=15):
    data = urllib.parse.urlencode(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        headers=headers or {},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=timeout) as result:
        return json.loads(result.read().decode("utf-8"))


def graph_token():
    secret = secrets.get_secret_value(
        SecretId=os.environ["GRAPH_SECRET_ARN"]
    )

    credentials = json.loads(secret["SecretString"])

    tenant_id = credentials["tenant_id"]
    client_id = credentials["client_id"]
    client_secret = credentials["client_secret"]

    token_url = (
        "https://login.microsoftonline.com/"
        + urllib.parse.quote(tenant_id, safe="")
        + "/oauth2/v2.0/token"
    )

    result = post_json(
        token_url,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=15,
    )

    return result["access_token"]


def send_graph_mail(token, recipient, subject, message):
    mailbox = urllib.parse.quote(
        os.environ["GRAPH_MAILBOX"], safe=""
    )

    url = (
        "https://graph.microsoft.com/v1.0/users/"
        + mailbox
        + "/sendMail"
    )

    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "Text",
                "content": message,
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": recipient
                    }
                }
            ],
        },
        "saveToSentItems": True,
    }

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=20) as result:
        if result.status != 202:
            raise RuntimeError("GRAPH_SEND_NOT_ACCEPTED")


def claim_delivery(payload):
    event_id = str(uuid.UUID(payload["event_id"]))
    approval_id = str(uuid.UUID(payload["approval_id"]))
    case_id = payload["case_id"]

    connection = db_connect()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                c.case_id,
                c.supplier_email,
                c.email_subject,
                c.approved_reply,
                c.processing_status,
                d.approval_id
            FROM human_review_cases c
            JOIN human_review_decisions d
              ON d.case_id = c.case_id
            JOIN human_review_outbox o
              ON o.approval_id = d.approval_id
            WHERE c.case_id = %s
              AND d.approval_id = %s
              AND o.event_id = %s
              AND d.decision IN (
                'APPROVED',
                'EDITED_AND_APPROVED'
              )
            FOR UPDATE OF c
            """,
            (case_id, approval_id, event_id),
        )

        row = cursor.fetchone()

        if not row:
            raise ValueError("APPROVED_EVENT_NOT_FOUND")

        (
            db_case_id,
            recipient,
            subject,
            approved_reply,
            processing_status,
            db_approval_id,
        ) = row

        if not approved_reply or not approved_reply.strip():
            raise ValueError("APPROVED_REPLY_MISSING")

        cursor.execute(
            """
            SELECT delivery_status
            FROM human_review_delivery_attempts
            WHERE event_id = %s
            """,
            (event_id,),
        )

        existing = cursor.fetchone()

        if existing:
            connection.commit()
            return None

        if processing_status not in (
            "APPROVED",
            "REPLY_QUEUED",
        ):
            raise ValueError("CASE_NOT_READY_FOR_REPLY")

        cursor.execute(
            """
            INSERT INTO human_review_delivery_attempts (
                event_id,
                approval_id,
                case_id,
                delivery_status
            )
            VALUES (%s, %s, %s, 'CLAIMED')
            """,
            (event_id, approval_id, case_id),
        )

        cursor.execute(
            """
            UPDATE human_review_cases
            SET processing_status = 'REPLY_SENDING',
                updated_at = CURRENT_TIMESTAMP
            WHERE case_id = %s
            """,
            (case_id,),
        )

        connection.commit()

        return {
            "event_id": event_id,
            "case_id": db_case_id,
            "recipient": recipient,
            "subject": subject or "Payment Status Update",
            "reply": approved_reply,
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def update_delivery(event_id, case_id, status):
    connection = db_connect()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE human_review_delivery_attempts
            SET delivery_status = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE event_id = %s
              AND delivery_status = 'CLAIMED'
            """,
            (status, event_id),
        )

        if cursor.rowcount != 1:
            raise RuntimeError("DELIVERY_STATE_CONFLICT")

        if status == "ACCEPTED":
            case_status = "REPLY_SENT"
        else:
            case_status = "REPLY_FAILED"

        cursor.execute(
            """
            UPDATE human_review_cases
            SET processing_status = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE case_id = %s
            """,
            (case_status, case_id),
        )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def process_message(payload):
    if os.environ.get("REPLY_SEND_ENABLED") != "true":
        raise RuntimeError("REPLY_SENDING_DISABLED")

    delivery = claim_delivery(payload)

    if delivery is None:
        logger.info(
            "Delivery already claimed; skipping duplicate"
        )
        return

    # Once CLAIMED is committed, an uncertain failure must
    # not cause an automatic second send.
    try:
        token = graph_token()

        send_graph_mail(
            token,
            delivery["recipient"],
            delivery["subject"],
            delivery["reply"],
        )

    except Exception:
        logger.exception(
            "Graph delivery outcome requires reconciliation"
        )

        # This status means the email may or may not have
        # been accepted. Do not automatically retry.
        update_delivery(
            delivery["event_id"],
            delivery["case_id"],
            "UNCERTAIN",
        )
        return

    update_delivery(
        delivery["event_id"],
        delivery["case_id"],
        "ACCEPTED",
    )

    logger.info(
        "Graph accepted reply for case=%s",
        delivery["case_id"],
    )


def lambda_handler(event, context):
    failures = []

    for record in event.get("Records", []):
        try:
            payload = json.loads(record["body"])

            if not isinstance(payload, dict):
                raise ValueError("INVALID_MESSAGE")

            process_message(payload)

        except Exception:
            logger.exception("Reply message processing failed")

            failures.append({
                "itemIdentifier": record["messageId"]
            })

    return {"batchItemFailures": failures}
