"""Shared test helpers: build synthetic Teams session states."""

from __future__ import annotations

import base64
import json
import time
from typing import Any


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def make_jwt(claims: dict[str, Any], *, exp_in: int = 3600) -> str:
    header = b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = dict(claims)
    payload.setdefault("exp", int(time.time()) + exp_in)
    body = b64url(json.dumps(payload).encode())
    return f"{header}.{body}.sig"


def msal_entry(target: str, token: str) -> dict[str, str]:
    return {
        "name": f"{target}-accesstoken",
        "value": json.dumps({"credentialType": "AccessToken", "target": target, "secret": token}),
    }


def tmp_auth_entry(resource: str, token: str, *, key_b64: str | None = None) -> dict[str, str]:
    return {
        "name": f"tmp.auth.v1.user.Token.{resource}",
        "value": json.dumps({"item": {"token": token, "expires": int(time.time()) + 3600}}),
    }


def region_entry(middle_tier: str, chatsvc: str, csa: str | None = None) -> dict[str, str]:
    return {
        "name": "tmp.auth.v1.user.Discover.DISCOVER-REGION-GTM",
        "value": json.dumps(
            {
                "item": {
                    "middleTier": middle_tier,
                    "chatServiceAfd": chatsvc,
                    "chatSvcAggAfd": csa or chatsvc.replace("chatsvc", "csa"),
                }
            }
        ),
    }


def user_details_entry(mri: str, region: str = "amer") -> dict[str, str]:
    return {
        "name": "tmp.auth.v1.user.Discover.DISCOVER-USER-DETAILS",
        "value": json.dumps(
            {
                "item": {
                    "id": mri,
                    "region": region,
                    "licenseDetails": {"isTranscriptEnabled": True},
                }
            }
        ),
    }


def make_session(
    *,
    origin: str = "https://teams.microsoft.com",
    local_storage: list[dict[str, str]] | None = None,
    cookies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "cookies": cookies or [],
        "origins": [{"origin": origin, "localStorage": local_storage or []}],
    }


def cookie(name: str, value: str, domain: str) -> dict[str, Any]:
    return {"name": name, "value": value, "domain": domain, "path": "/"}
