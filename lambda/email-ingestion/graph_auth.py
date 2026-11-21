"""Acquires Microsoft Graph access tokens for the mailbox application.

Uses the OAuth2 client-credentials flow, so the app reads the mailbox without a
signed-in user. Credentials live in AWS Secrets Manager and are never held in
environment variables, where get-function-configuration would expose them.

Tokens last about an hour and are cached in memory, which a warm Lambda reuses
across invocations.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

import boto3

TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
SCOPE = "https://graph.microsoft.com/.default"
# Refresh early so a token cannot expire mid-request.
EXPIRY_MARGIN_SECONDS = 120


class GraphAuthError(RuntimeError):
    """Raised when a Graph access token cannot be obtained."""


def load_credentials(secret_id: Optional[str] = None) -> Dict[str, Any]:
    """Read the Azure AD application credentials from Secrets Manager."""
    secret_id = secret_id or os.environ["GRAPH_SECRET_ID"]
    client = boto3.client("secretsmanager", region_name=os.getenv("AWS_REGION", "us-east-1"))
    try:
        payload = client.get_secret_value(SecretId=secret_id)["SecretString"]
    except Exception as exc:
        raise GraphAuthError(f"Could not read secret {secret_id}: {exc}") from exc
    credentials = json.loads(payload)
    for field in ("tenant_id", "client_id", "client_secret"):
        if not credentials.get(field):
            raise GraphAuthError(f"Secret {secret_id} is missing {field}")
    return credentials


class GraphTokenProvider:
    """Fetches and caches an application token for Microsoft Graph."""

    def __init__(self, credentials: Optional[Dict[str, Any]] = None, timeout: int = 30):
        self._credentials = credentials
        self._timeout = timeout
        self._token: Optional[str] = None
        self._expires_at: float = 0.0

    @property
    def credentials(self) -> Dict[str, Any]:
        if self._credentials is None:
            self._credentials = load_credentials()
        return self._credentials

    def token(self) -> str:
        if self._token and time.time() < self._expires_at:
            return self._token

        credentials = self.credentials
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": credentials["client_id"],
                "client_secret": credentials["client_secret"],
                "scope": SCOPE,
            }
        ).encode()

        url = TOKEN_URL.format(tenant=credentials["tenant_id"])
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=self._timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = json.loads(exc.read().decode() or "{}").get("error", "unknown")
            raise GraphAuthError(f"Token request rejected: {detail}") from exc
        except Exception as exc:
            raise GraphAuthError(f"Token request failed: {exc}") from exc

        self._token = payload["access_token"]
        self._expires_at = time.time() + int(payload.get("expires_in", 3600)) - EXPIRY_MARGIN_SECONDS
        return self._token
