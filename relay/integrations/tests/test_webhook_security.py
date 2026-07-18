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
        self._app_secret = app_secret

    def get_app_secret(self) -> str:
        return self._app_secret

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

    def test_missing_secret_allows_through(self):
        payload = b'{"object":"whatsapp_business_account"}'
        signature = "sha256=anything"

        adapter = MetaCloudAPIAdapter(_MockAccount(""))
        self.assertTrue(adapter.validate_webhook_signature(payload, signature))

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
