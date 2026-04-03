"""Firebase Cloud Messaging integration for push notifications."""

import base64
import json
import logging

import firebase_admin
import firebase_admin.messaging
from firebase_admin import credentials

from app.core.config import settings

logger = logging.getLogger(__name__)

_firebase_app = None


def init_firebase() -> None:
    """Initialize Firebase Admin SDK from base64-encoded credentials."""
    global _firebase_app

    if _firebase_app is not None:
        return

    if not settings.firebase_credentials_json:
        logger.warning("FIREBASE_CREDENTIALS_JSON not set — push notifications disabled")
        return

    try:
        decoded = base64.b64decode(settings.firebase_credentials_json)
        service_account = json.loads(decoded)
        cred = credentials.Certificate(service_account)
        _firebase_app = firebase_admin.initialize_app(cred)
        logger.info("Firebase initialized successfully")
    except Exception as e:
        logger.warning("Firebase initialization failed — push notifications disabled: %s", e)


def send_push(
    token: str,
    title: str,
    body: str,
    data: dict | None = None,
    ios_interruption_level: str = "active",
    sound: str | None = None,
) -> bool:
    """Send a push notification to a single device. Returns True on success."""
    if _firebase_app is None:
        logger.debug("Firebase not initialized, skipping push to %s...", token[:8])
        return False

    try:
        message = firebase_admin.messaging.Message(
            notification=firebase_admin.messaging.Notification(title=title, body=body),
            token=token,
            data=data,
            apns=firebase_admin.messaging.APNSConfig(
                payload=firebase_admin.messaging.APNSPayload(
                    aps=firebase_admin.messaging.Aps(
                        sound=sound or "default",
                    ),
                    custom_data={"interruption-level": ios_interruption_level},
                ),
            ),
            android=firebase_admin.messaging.AndroidConfig(priority="high"),
        )
        firebase_admin.messaging.send(message)
        return True
    except Exception as e:
        logger.exception("Failed to send push to %s...: %s", token[:8], e)
        return False


def send_push_batch(
    tokens: list[str],
    title: str,
    body: str,
    data: dict | None = None,
    ios_interruption_level: str = "active",
    sound: str | None = None,
) -> int:
    """Send push notification to multiple devices. Returns count of successful sends."""
    if _firebase_app is None:
        logger.debug("Firebase not initialized, skipping batch push")
        return 0

    if not tokens:
        return 0

    try:
        messages = []
        for token in tokens:
            messages.append(
                firebase_admin.messaging.Message(
                    notification=firebase_admin.messaging.Notification(title=title, body=body),
                    token=token,
                    data=data,
                    apns=firebase_admin.messaging.APNSConfig(
                        payload=firebase_admin.messaging.APNSPayload(
                            aps=firebase_admin.messaging.Aps(
                                sound=sound or "default",
                            ),
                            custom_data={"interruption-level": ios_interruption_level},
                        ),
                    ),
                    android=firebase_admin.messaging.AndroidConfig(priority="high"),
                )
            )
        response = firebase_admin.messaging.send_each(messages)
        return response.success_count
    except Exception as e:
        logger.exception("Batch push failed: %s", e)
        return 0
