
import base64
import json
import logging
import os
import uuid
from datetime import datetime
from decimal import Decimal

import boto3
import pg8000.dbapi

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

rds = boto3.client("rds")


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


def json_encoder(value):
    if isinstance(value, (datetime, uuid.UUID, Decimal)):
        return str(value)

    raise TypeError(type(value).__name__)


def response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(payload, default=json_encoder),
    }


def fetch_dicts(cursor):
    columns = [column[0] for column in cursor.description]

    return [
        dict(zip(columns, row))
        for row in cursor.fetchall()
    ]


def get_reviewer(event):
    authorizer = (
        (event.get("requestContext") or {})
        .get("authorizer") or {}
    )

    claims = authorizer.get("claims") or {}

    return claims.get("sub")


def list_pending_cases():
    connection = connect_db()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                case_id,
                message_id,
                invoice_id,
                supplier_email,
                email_subject,
                investigation_status,
                processing_status,
                created_at
            FROM human_review_cases
            WHERE processing_status = 'PENDING_REVIEW'
            ORDER BY created_at
            LIMIT 100
            """
        )

        return response(
            200,
            {"cases": fetch_dicts(cursor)},
        )

    finally:
        connection.close()


def get_case(case_id):
    connection = connect_db()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                case_id,
                message_id,
                conversation_id,
                invoice_id,
                supplier_email,
                email_subject,
                investigation_status,
                investigation_result,
                draft_reply,
                approved_reply,
                processing_status,
                created_at,
                updated_at
            FROM human_review_cases
            WHERE case_id = %s
            """,
            (case_id,),
        )

        cases = fetch_dicts(cursor)

        if not cases:
            return response(
                404,
                {"error": "CASE_NOT_FOUND"},
            )

        return response(200, {"case": cases[0]})

    finally:
        connection.close()


def review_case(case_id, reviewer_id, body):
    decision = body.get("decision")
    edited_reply = body.get("edited_reply")

    if decision not in (
        "APPROVED",
        "EDITED_AND_APPROVED",
        "REJECTED",
    ):
        return response(
            400,
            {"error": "INVALID_DECISION"},
        )

    if decision == "EDITED_AND_APPROVED":
        if (
            not isinstance(edited_reply, str)
            or not edited_reply.strip()
        ):
            return response(
                400,
                {"error": "EDITED_REPLY_REQUIRED"},
            )

    connection = connect_db()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                case_id,
                message_id,
                invoice_id,
                draft_reply,
                investigation_status,
                processing_status
            FROM human_review_cases
            WHERE case_id = %s
            FOR UPDATE
            """,
            (case_id,),
        )

        cases = fetch_dicts(cursor)

        if not cases:
            connection.rollback()
            return response(
                404,
                {"error": "CASE_NOT_FOUND"},
            )

        case = cases[0]

        if case["processing_status"] != "PENDING_REVIEW":
            connection.rollback()
            return response(
                409,
                {"error": "CASE_ALREADY_REVIEWED"},
            )

        if case["investigation_status"] != "COMPLETED":
            connection.rollback()
            return response(
                409,
                {"error": "INVESTIGATION_NOT_COMPLETED"},
            )

        original_draft = case["draft_reply"]

        if decision == "REJECTED":
            final_reply = None
            new_status = "REJECTED"

        elif decision == "EDITED_AND_APPROVED":
            final_reply = edited_reply.strip()
            new_status = "APPROVED"

        else:
            if (
                not isinstance(original_draft, str)
                or not original_draft.strip()
            ):
                connection.rollback()
                return response(
                    409,
                    {"error": "DRAFT_REPLY_MISSING"},
                )

            final_reply = original_draft
            new_status = "APPROVED"

        approval_id = str(uuid.uuid4())

        cursor.execute(
            """
            INSERT INTO human_review_decisions (
                approval_id,
                case_id,
                reviewer_id,
                decision,
                original_draft,
                final_reply
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                approval_id,
                case_id,
                reviewer_id,
                decision,
                original_draft,
                final_reply,
            ),
        )

        cursor.execute(
            """
            UPDATE human_review_cases
            SET
                processing_status = %s,
                approved_reply = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE case_id = %s
            """,
            (new_status, final_reply, case_id),
        )

        if decision != "REJECTED":
            event_id = str(uuid.uuid4())

            event_payload = {
                "event_id": event_id,
                "approval_id": approval_id,
                "case_id": case_id,
                "message_id": case["message_id"],
                "invoice_id": case["invoice_id"],
                "event_type": "SUPPLIER_REPLY_APPROVED",
            }

            cursor.execute(
                """
                INSERT INTO human_review_outbox (
                    event_id,
                    approval_id,
                    case_id,
                    event_type,
                    event_payload
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    event_id,
                    approval_id,
                    case_id,
                    "SUPPLIER_REPLY_APPROVED",
                    json.dumps(event_payload),
                ),
            )

        connection.commit()

        logger.info(
            "Review saved: case=%s decision=%s",
            case_id,
            decision,
        )

        return response(
            200,
            {
                "case_id": case_id,
                "approval_id": approval_id,
                "decision": decision,
                "processing_status": new_status,
                "reply_queued": False,
            },
        )

    except Exception:
        connection.rollback()
        logger.exception("Review transaction failed")

        return response(
            500,
            {"error": "INTERNAL_SERVER_ERROR"},
        )

    finally:
        connection.close()


def lambda_handler(event, context):
    reviewer_id = get_reviewer(event)

    if not reviewer_id:
        return response(
            401,
            {"error": "UNAUTHENTICATED"},
        )

    method = (event.get("httpMethod") or "").upper()
    resource = event.get("resource") or ""

    path_parameters = event.get("pathParameters") or {}
    case_id = path_parameters.get("case_id")

    try:
        if (
            method == "GET"
            and resource == "/pending-review-cases"
        ):
            return list_pending_cases()

        if (
            method == "GET"
            and resource == "/review-cases/{case_id}"
            and case_id
        ):
            return get_case(case_id)

        if (
            method == "POST"
            and resource == "/review-cases/{case_id}/decision"
            and case_id
        ):
            try:
                raw_body = event.get("body") or "{}"

                if event.get("isBase64Encoded"):
                    raw_body = base64.b64decode(
                        raw_body,
                        validate=True,
                    ).decode("utf-8")

                body = json.loads(raw_body)

                if not isinstance(body, dict):
                    raise ValueError("Expected JSON object")

            except (ValueError, TypeError):
                return response(
                    400,
                    {"error": "INVALID_JSON"},
                )

            return review_case(
                case_id,
                reviewer_id,
                body,
            )

        return response(
            404,
            {"error": "ROUTE_NOT_FOUND"},
        )

    except Exception:
        logger.exception("Human Review Backend failed")

        return response(
            500,
            {"error": "INTERNAL_SERVER_ERROR"},
        )
