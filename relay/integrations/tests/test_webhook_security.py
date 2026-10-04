# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Tests for webhook signature verification and idempotency."""

import hashlib
import hmac
import json
import unittest

from relay.integrations.meta_cloud_api import MetaCloudAPIAdapter


class _MockAccount:
    def __init__(self, app_secret: str = ""):
        self._fields = {"app_secret": app_secret or None}

    def get(self, key, default=None):
        return self._fields.get(key, default)

    def get_app_secret(self) -> str:
        return self._fields.get("app_secret") or ""

    def get_access_token(self) -> str:
        return "test_token"

    def get_api_base_url(self) -> str:
        return "https://graph.facebook.com/v20.0"

    @property
    def phone_number_id(self):
        return "12345"


class TestMetaWebhookSignature(unittest.TestCase):
    def test_valid_signature(self):
        secret = "test_secret"
        payload = b'{"object":"whatsapp_business_account"}'
        signature = "sha256=" + hmac.new(
            secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()

        adapter = MetaCloudAPIAdapter(_MockAccount(secret))
        self.assertTrue(adapter.validate_webhook_signature(payload, signature))

    def test_invalid_signature(self):
        secret = "test_secret"
        payload = b'{"object":"whatsapp_business_account"}'
        signature = "sha256=invalidhex"

        adapter = MetaCloudAPIAdapter(_MockAccount(secret))
        self.assertFalse(adapter.validate_webhook_signature(payload, signature))

    def test_a_signature_cannot_be_verified_without_a_secret(self):
        """#12: the old behaviour here was allow-through-with-a-warning,
        pinned by a test whose mock returned "" without throwing. A real
        Relay Account without the secret makes get_password throw -- and a
        signature that cannot be verified is not accepted."""
        payload = b'{"object":"whatsapp_business_account"}'
        signature = "sha256=anything"

        adapter = MetaCloudAPIAdapter(_MockAccount(""))
        self.assertFalse(adapter.validate_webhook_signature(payload, signature))

    def test_a_meta_account_requires_a_valid_signature(self):
        """#12: Meta's adapter overrides the base default -- an unsigned
        request on a guest-callable endpoint is refused."""
        adapter = MetaCloudAPIAdapter(_MockAccount("test_secret"))
        self.assertTrue(adapter.requires_valid_signature())

    def test_signature_case_mismatch(self):
        secret = "test_secret"
        payload = b'{"object":"whatsapp_business_account"}'
        signature = "SHA256=" + hmac.new(
            secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()

        adapter = MetaCloudAPIAdapter(_MockAccount(secret))
        self.assertFalse(adapter.validate_webhook_signature(payload, signature))


if __name__ == "__main__":
    unittest.main()
