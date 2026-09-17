"""Durable per-device FCM delivery state machine.

The API only creates delivery rows. This module is worker-owned: it verifies that
an installation is still deliverable, builds a trusted FCM payload, sends once,
and records success/retry/dead/skip state without leaking raw tokens or patient
notification content into logs or stored provider errors.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from datetime import timedelta
from typing import Literal

from firebase_admin import messaging
from sqlmodel import Session

from app.core.config import settings
from app.core.firebase import is_fcm_available
from app.core.time import utc_now
from app.db.models import (
    DeliveryStatus,
    FCMToken,
    Notification,
    NotificationDelivery,
)
from app.services.device_service import skip_unresolved_deliveries


FailureCategory = Literal["permanent_token", "transient", "malformed"]


class _FCMUnavailable(RuntimeError):
    """Internal marker for retryable local/provider unavailability."""


def build_push_payload(notification: Notification) -> dict[str, str]:
    """Build FCM data, with trusted server identifiers overriding stored data."""
    raw: dict = {}
    if notification.data:
        decoded = json.loads(notification.data)
        if not isinstance(decoded, dict):
            raise ValueError("notification data must be a JSON object")
        raw = decoded

    payload: dict[str, str] = {}
    for key, value in raw.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            payload[str(key)] = json.dumps(value, separators=(",", ":"), sort_keys=True)
        else:
            payload[str(key)] = str(value)

    # Never trust duplicated identifiers stored in the free-form deep-link data.
    payload["notification_id"] = str(notification.id)
    payload["event_id"] = notification.event_id
    payload["type"] = notification.type
    return payload


def classify_delivery_failure(exc: Exception) -> FailureCategory:
    """Classify failures without depending tightly on Firebase private classes."""
    name = type(exc).__name__
    if name in {
        "UnregisteredError",
        "SenderIdMismatchError",
        "ThirdPartyAuthError",
    }:
        return "permanent_token"
    if isinstance(exc, (json.JSONDecodeError, ValueError, TypeError)):
        return "malformed"
    # Network errors, provider availability/quota errors, and unknown Firebase
    # exceptions are retried. Unknown provider failures are safer to retry than
    # to silently discard a blood-request alert.
    return "transient"


def retry_delay_seconds(attempt: int, *, jitter: float | None = None) -> float:
    """Return configured retry delay for a 1-based failed attempt."""
    schedule = settings.notification_worker_retry_seconds
    if not schedule:
        schedule = [60, 300, 1800, 7200, 43200]
    index = min(max(attempt, 1) - 1, len(schedule) - 1)
    base = float(schedule[index])
    if jitter is None:
        # Bounded positive jitter avoids synchronized retry spikes while never
        # retrying earlier than the configured base delay.
        jitter = random.uniform(0.0, min(base * 0.1, 30.0))
    return base + max(float(jitter), 0.0)


def _finish(
    session: Session,
    delivery: NotificationDelivery,
    *,
    status: DeliveryStatus,
    category: str | None = None,
    error: str | None = None,
) -> None:
    delivery.status = status.value
    delivery.last_error_category = category
    delivery.last_error = error
    delivery.locked_at = None
    delivery.locked_by = None
    delivery.updated_at = utc_now()
    session.add(delivery)


def process_delivery(
    session: Session,
    delivery_id: int,
    *,
    send: Callable | None = None,
    jitter: float | None = None,
) -> None:
    """Attempt one durable delivery and persist the resulting terminal/retry state."""
    delivery = session.get(NotificationDelivery, delivery_id)
    if delivery is None:
        return
    if delivery.status in {
        DeliveryStatus.DELIVERED.value,
        DeliveryStatus.DEAD.value,
        DeliveryStatus.SKIPPED.value,
    }:
        return

    notification = session.get(Notification, delivery.notification_id)
    device = session.get(FCMToken, delivery.fcm_token_id)

    if notification is None or device is None:
        delivery.attempts += 1
        _finish(
            session,
            delivery,
            status=DeliveryStatus.DEAD,
            category="malformed",
            error="missing_delivery_dependency",
        )
        session.commit()
        return

    if (
        not device.is_active
        or not device.token
        or device.user_id != notification.user_id
    ):
        _finish(
            session,
            delivery,
            status=DeliveryStatus.SKIPPED,
            category="device_not_deliverable",
            error="device_not_deliverable",
        )
        session.commit()
        return

    delivery.attempts += 1
    delivery.updated_at = utc_now()
    session.add(delivery)

    try:
        payload = build_push_payload(notification)
        message = messaging.Message(
            notification=messaging.Notification(
                title=notification.title,
                body=notification.body,
            ),
            data=payload,
            token=device.token,
        )
        if send is None:
            if not is_fcm_available():
                raise _FCMUnavailable("firebase_unavailable")
            send = messaging.send
        provider_message_id = send(message)
    except Exception as exc:  # provider boundary; state machine classifies below
        category = classify_delivery_failure(exc)
        # Store only the exception class. Provider messages can contain request
        # details and must not become an accidental sensitive-data log/audit sink.
        safe_error = type(exc).__name__
        now = utc_now()

        if category == "permanent_token":
            # Skip this and every other unresolved job before detaching the token
            # so no historical notification can follow it to another account.
            skip_unresolved_deliveries(session, device.id, "permanent_token")
            device.token = None
            device.is_active = False
            device.disabled_at = now
            device.last_failure_reason = "permanent_token"
            device.updated_at = now
            session.add(device)
            # skip_unresolved_deliveries already made this row terminal; retain
            # the actual attempt count from this provider call.
            delivery.last_error = safe_error
            delivery.last_error_category = "permanent_token"
            delivery.updated_at = now
            session.add(delivery)
        elif category == "malformed":
            _finish(
                session,
                delivery,
                status=DeliveryStatus.DEAD,
                category="malformed",
                error=safe_error,
            )
        else:
            max_attempts = max(settings.notification_worker_max_attempts, 1)
            if delivery.attempts >= max_attempts:
                _finish(
                    session,
                    delivery,
                    status=DeliveryStatus.DEAD,
                    category="transient",
                    error=safe_error,
                )
            else:
                _finish(
                    session,
                    delivery,
                    status=DeliveryStatus.RETRY,
                    category="transient",
                    error=safe_error,
                )
                delivery.available_at = now + timedelta(
                    seconds=retry_delay_seconds(delivery.attempts, jitter=jitter)
                )
                session.add(delivery)
        session.commit()
        return

    delivery.status = DeliveryStatus.DELIVERED.value
    delivery.provider_message_id = str(provider_message_id)
    delivery.delivered_at = utc_now()
    delivery.last_error = None
    delivery.last_error_category = None
    delivery.locked_at = None
    delivery.locked_by = None
    delivery.updated_at = utc_now()
    session.add(delivery)
    session.commit()
