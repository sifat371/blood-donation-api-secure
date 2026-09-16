#!/usr/bin/env python
"""
Smoke-check a *running* backend over HTTP.

Not a pytest test — it needs a live server, so it lived in tests/ only to break
`pytest` at collection time (it matched the `*_test.py` pattern and fired real
requests during import). Run it by hand after starting uvicorn:

    python scripts/smoke_check.py
    API_BASE_URL=http://192.168.0.104:8000 python scripts/smoke_check.py

The base URL is configurable so this can verify the address a physical device
actually uses, not just localhost. Exits non-zero on the first failure.
"""

import os
import sys

import httpx

BASE = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/") + "/api/v1"
TIMEOUT = 10.0


def check(label: str, response: httpx.Response, describe=None) -> dict:
    """Print one line per call and abort on anything that isn't a 2xx."""
    ok = 200 <= response.status_code < 300
    body = {}
    try:
        body = response.json()
    except ValueError:
        pass
    detail = ""
    if ok and describe:
        detail = f"  {describe(body)}"
    elif not ok:
        detail = f"  {str(body)[:200]}"
    print(f"{'[OK]  ' if ok else '[FAIL]'} {label}: {response.status_code}{detail}")
    if not ok:
        sys.exit(1)
    return body


def main() -> None:
    print(f"Checking {BASE}\n")

    try:
        health = httpx.get(BASE.replace("/api/v1", "/health"), timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        print(f"[FAIL] Cannot reach the server at {BASE}: {type(exc).__name__}")
        print("       Start it with: uvicorn app.main:app --host 0.0.0.0 --port 8000")
        sys.exit(1)
    check(
        "Health",
        health,
        lambda b: f"env={b.get('environment')} push={b.get('push_notifications')}",
    )

    tokens = check(
        "Dev login",
        httpx.post(
            f"{BASE}/auth/dev-login",
            json={"email": "rahim@example.com", "name": "Rahim"},
            timeout=TIMEOUT,
        ),
        lambda b: f"profile_incomplete={b.get('profile_incomplete')}",
    )
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    check(
        "Profile",
        httpx.get(f"{BASE}/profile/me", headers=headers, timeout=TIMEOUT),
        lambda b: f"{b.get('name')} {b.get('blood_group')}",
    )

    check(
        "Donor search",
        httpx.get(
            f"{BASE}/donors/search",
            params={"blood_group": "O+", "latitude": 23.7461, "longitude": 90.3742},
            headers=headers,
            timeout=TIMEOUT,
        ),
        lambda b: f"{b.get('total')} found",
    )

    check(
        "Notifications",
        httpx.get(f"{BASE}/notifications", headers=headers, timeout=TIMEOUT),
        lambda b: f"{b.get('total')} total",
    )

    check(
        "Chat",
        httpx.post(
            f"{BASE}/chat/message",
            json={"message": "Am I eligible to donate?"},
            headers=headers,
            timeout=30.0,  # the Gemini round-trip can be slow
        ),
        lambda b: b.get("content", "")[:80].encode("ascii", "replace").decode(),
    )

    check(
        "Chat history",
        httpx.get(f"{BASE}/chat/history", headers=headers, timeout=TIMEOUT),
        lambda b: f"{len(b.get('messages', []))} messages",
    )

    check(
        "My requests",
        httpx.get(f"{BASE}/blood-requests/mine", headers=headers, timeout=TIMEOUT),
        lambda b: f"{b.get('total')} requests",
    )

    check(
        "Nearby requests",
        httpx.get(
            f"{BASE}/blood-requests/nearby",
            params={"latitude": 23.7461, "longitude": 90.3742},
            headers=headers,
            timeout=TIMEOUT,
        ),
        lambda b: f"{b.get('total')} nearby",
    )

    check(
        "Donation history",
        httpx.get(f"{BASE}/donation-history", headers=headers, timeout=TIMEOUT),
        lambda b: f"{b.get('total')} records",
    )

    print("\n[OK] Smoke check passed.")


if __name__ == "__main__":
    main()
