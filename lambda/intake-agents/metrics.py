"""Publishing numbers to CloudWatch so they can be graphed and alarmed on.

Two kinds of number live here:

    operational   how long each email took, emitted as the pipeline runs
    quality       how accurate the classifier is, emitted by the eval Lambda

Both go to one namespace so a single CloudWatch dashboard can show the system
working and the system being right side by side.

A failure to publish is swallowed. A metric is an observation about the work,
never the work itself, so losing one must not fail an email.
"""

import logging
import os
from typing import Optional

import boto3

logger = logging.getLogger(__name__)

NAMESPACE = "PaymentQueryPlatform"

_client = None


def _cloudwatch():
    global _client
    if _client is None:
        _client = boto3.client("cloudwatch", region_name=os.getenv("AWS_REGION", "us-east-1"))
    return _client


def put(name: str, value: float, unit: str = "None", stage: Optional[str] = None) -> None:
    """Emit one metric. Stage, when given, is a dimension so stages compare."""
    dimensions = [{"Name": "Stage", "Value": stage}] if stage else []
    try:
        _cloudwatch().put_metric_data(
            Namespace=NAMESPACE,
            MetricData=[{"MetricName": name, "Value": value, "Unit": unit, "Dimensions": dimensions}],
        )
    except Exception as exc:
        logger.warning("Could not publish metric %s: %s", name, exc)


def seconds(name: str, value: float, stage: Optional[str] = None) -> None:
    put(name, value, unit="Seconds", stage=stage)


def percent(name: str, value: float) -> None:
    put(name, value, unit="Percent")


def count(name: str, value: float = 1) -> None:
    put(name, value, unit="Count")
