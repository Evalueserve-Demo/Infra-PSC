"""The results endpoint: what the pipeline concluded for each mail, and why.

A read-only Lambda, reached through a Function URL or invoked directly. It runs
the same query as scripts/show_results.py, so a tester who cannot run the
script sees the same thing.

    GET <url>                     every mail, as JSON
    GET <url>?format=csv          the same rows as a spreadsheet
    GET <url>?case=PSQ-2026-000012  the full detail for one case: classification,
                                  findings, routing and the draft reply

The same parameters work in a direct invocation: {"queryStringParameters": {...}}
or the bare {"case": "..."} form.

{"export": "1"} writes the CSV to the results bucket instead (results/latest.csv, and
the day's copy under results/daily/), with what the pipeline stored for each mail added as
columns. The scheduler does this every 15 minutes, so
people read a file in S3, where access is controlled by the bucket's permissions.

It only reads. The database user needs SELECT on the ps tables, which it already
has, and the function's role needs rds-db:connect.

Environment:
    AURORA_HOST   cluster endpoint
    AURORA_DB     database name, defaults to appdb
    AURORA_USER   database user, defaults to integ_app
    RESULTS_BUCKET  the bucket the export is written to
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import boto3

from results import (
    CASE_SQL, COLUMNS, LIST_SQL, case_detail, fetch, summarise, to_csv,
)
from database import Database

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# A database that has been idle can time out on the first connection and answer
# the second. One retry covers it, rather than handing the caller an error.
ATTEMPTS = 2

_database: Database | None = None


def _get_database() -> Database:
    global _database
    if _database is None:
        _database = Database()
    return _database


def _query(sql: str, params: tuple = ()):
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return fetch(_get_database(), sql, params)
        except Exception as exc:
            logger.warning("Results query failed (attempt %d of %d): %s", attempt, ATTEMPTS, exc)
            _get_database().close()
            if attempt == ATTEMPTS:
                raise


def parameters(event: Any) -> Dict[str, str]:
    """The query parameters, from a Function URL request or a direct invocation."""
    event = event if isinstance(event, dict) else {}
    found = event.get("queryStringParameters") or {k: v for k, v in event.items() if isinstance(v, str)}
    return {k: str(v) for k, v in found.items()}


def _respond(status: int, body: str, content_type: str = "application/json") -> Dict[str, Any]:
    return {"statusCode": status, "headers": {"Content-Type": f"{content_type}; charset=utf-8"}, "body": body}


def _json(status: int, payload: Any) -> Dict[str, Any]:
    return _respond(status, json.dumps(payload, default=str, indent=2))


def export(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Write the report as a CSV to the results bucket: the latest, and the day's copy."""
    body = to_csv(rows, COLUMNS).encode("utf-8")
    keys = ["results/latest.csv", f"results/daily/{datetime.now(timezone.utc):%Y-%m-%d}.csv"]
    for key in keys:
        boto3.client("s3").put_object(Bucket=os.environ["RESULTS_BUCKET"], Key=key, Body=body,
                                      ContentType="text/csv; charset=utf-8")
    return {"exported": len(rows), "keys": keys}


def lambda_handler(event: Any, context: Any = None) -> Dict[str, Any]:
    params = parameters(event)

    if params.get("case"):
        rows = _query(CASE_SQL, (params["case"],))
        if not rows:
            return _json(404, {"error": f"No case numbered {params['case']}."})
        return _json(200, {"case": params["case"], "mails": [case_detail(raw) for raw in rows]})

    raws = _query(LIST_SQL)
    if params.get("export"):
        return _json(200, export([summarise(raw, outputs=True) for raw in raws]))
    rows = [summarise(raw) for raw in raws]
    if params.get("format") == "csv":
        return _respond(200, to_csv(rows), "text/csv")
    return _json(200, {"count": len(rows), "mails": rows})
