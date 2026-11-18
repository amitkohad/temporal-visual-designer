"""Operator helper: Entra browser config + one signed Dev projects API call.

Does not invent tenant secrets. When the host has Entra disabled, exits 2 so
automation can treat live smoke as skipped. Local-demo token paste remains the
default path when Entra env is unset.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from urllib.parse import quote


class EntraSmokeError(Exception):
    """Safe smoke status without provider secrets or token material."""


def _get_json(url: str, *, headers: Mapping[str, str] | None = None) -> object:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", **dict(headers or {})},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")[:512]
        raise EntraSmokeError(f"HTTP {error.code} for {url}: {body}") from None
    except urllib.error.URLError as error:
        raise EntraSmokeError(f"Could not reach {url}: {error.reason}") from None


def fetch_browser_auth(base_url: str) -> dict[str, object]:
    root = base_url.rstrip("/")
    payload = _get_json(f"{root}/api/v1/auth/browser")
    if not isinstance(payload, dict):
        raise EntraSmokeError("Browser auth response was not a JSON object")
    return payload


def list_projects(
    base_url: str,
    *,
    organization_id: str,
    environment_id: str,
    token: str,
) -> list[object]:
    if not token.strip() or len(token) > 16_384:
        raise EntraSmokeError("Bearer token is required and must be bounded")
    root = base_url.rstrip("/")
    org = quote(organization_id, safe="")
    env = quote(environment_id, safe="")
    payload = _get_json(
        f"{root}/api/v1/organizations/{org}/environments/{env}/projects",
        headers={"Authorization": f"Bearer {token.strip()}"},
    )
    if isinstance(payload, list):
        return payload
    raise EntraSmokeError("Projects list response was not a JSON array")


def public_browser_summary(config: Mapping[str, object]) -> dict[str, object]:
    """Return only non-secret SPA fields suitable for operator logs."""
    if config.get("enabled") is not True:
        return {"enabled": False}
    allowed = (
        "enabled",
        "organization_id",
        "environment_id",
        "tenant_id",
        "spa_client_id",
        "authority",
        "scopes",
        "redirect_uri",
    )
    return {key: config[key] for key in allowed if key in config}


def run_smoke(
    *,
    base_url: str,
    token: str | None,
    check_browser_only: bool,
) -> int:
    config = fetch_browser_auth(base_url)
    summary = public_browser_summary(config)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary.get("enabled") is not True:
        print(
            "Entra browser auth is disabled on this host; local-demo token paste remains available.",
            file=sys.stderr,
        )
        return 2
    if check_browser_only:
        print("Browser auth config OK (no secrets returned).", file=sys.stderr)
        return 0
    if token is None or not token.strip():
        raise EntraSmokeError(
            "Pass --token with an Entra access token from Sign in with Microsoft "
            "(or MSAL) to call the projects API"
        )
    organization_id = summary.get("organization_id")
    environment_id = summary.get("environment_id")
    if not isinstance(organization_id, str) or not isinstance(environment_id, str):
        raise EntraSmokeError("Browser auth config missing organization/environment")
    projects = list_projects(
        base_url,
        organization_id=organization_id,
        environment_id=environment_id,
        token=token,
    )
    print(json.dumps({"projects_count": len(projects)}, indent=2))
    print("Entra-backed projects list OK.", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Critical-path Entra smoke: public browser auth config, then one signed "
            "Dev projects list. Does not claim live-tenant acceptance by itself."
        )
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Hosted Dev origin (default http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Entra access token from browser MSAL Connect (never commit this value)",
    )
    parser.add_argument(
        "--check-browser-only",
        action="store_true",
        help="Only verify GET /api/v1/auth/browser is enabled and secret-free",
    )
    args = parser.parse_args(argv)
    try:
        return run_smoke(
            base_url=args.base_url,
            token=args.token,
            check_browser_only=args.check_browser_only,
        )
    except EntraSmokeError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
