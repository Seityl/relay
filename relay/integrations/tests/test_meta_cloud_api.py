# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""The Meta Cloud API adapter's send-refusal classification (Seityl/relay#7).

Meta's error body carries a structured `error.code`; the documented codes
whose remedy is outside any retry loop are raised as PermanentRejection so
the queue refuses the row instead of backing off. The codes are read from
Meta's own error-codes page, not guessed; the classification is scoped to
SENDS -- `_post` is shared with `mark_read`, and a failed read-receipt must
never be mistaken for a refused message.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

import frappe
import requests

from relay.integrations.base_adapter import PermanentRejection
from relay.integrations.meta_cloud_api import MetaCloudAPIAdapter


class _MetaAccount:
	"""The few attributes the adapter reads off a Relay Account."""

	name = "Meta Refusal Test Account"
	phone_number_id = "123456789012345"

	def get_access_token(self):
		return "test-token"

	def get_api_base_url(self):
		return "https://graph.facebook.com/v20.0"


class _Response:
	def __init__(self, status_code, payload):
		self.status_code = status_code
		self._payload = payload
		self.text = json.dumps(payload)

	def json(self):
		return self._payload

	def raise_for_status(self):
		if self.status_code >= 400:
			raise requests.HTTPError(f"{self.status_code} Client Error", response=self)


def _meta_error(code, message="Meta says no"):
	"""Meta's documented error body shape."""
	return {"error": {"message": message, "type": "OAuthException", "code": code, "fbtrace_id": "Az"}}


class TestMetaSendRefusals(unittest.TestCase):
	#: https://developers.facebook.com/documentation/business-messaging/whatsapp/support/error-codes
	DOCUMENTED_PERMANENT = {
		131047: "more than 24 hours have passed since the recipient last replied -- send a template instead",
		130403: "the recipient has blocked this business; Meta says do not repeat the attempt",
		131050: "the recipient opted out of marketing messages; Meta says do not try again",
		131026: "message undeliverable -- not a WhatsApp number, terms not accepted, or client too old",
	}

	def setUp(self):
		self.adapter = MetaCloudAPIAdapter(_MetaAccount())
		self.queue_doc = MagicMock(
			message_type="Freeform",
			content_type="text",
			message_body="hello",
			media_url="",
			template="",
			template_parameters="",
			interactive_payload="",
			contact="contact-hash",
		)

	def _send_with(self, response):
		with (
			patch.object(MetaCloudAPIAdapter, "_resolve_recipient", return_value="15550001111"),
			patch("relay.integrations.meta_cloud_api.requests.post", return_value=response),
			patch("frappe.log_error"),
		):
			return self.adapter.send(self.queue_doc)

	def test_every_documented_deterministic_meta_code_is_a_permanent_rejection(self):
		for code, meaning in self.DOCUMENTED_PERMANENT.items():
			with self.subTest(code=code, meaning=meaning):
				with self.assertRaises(PermanentRejection) as refused:
					self._send_with(_Response(400, _meta_error(code, meaning)))
				self.assertIn(str(code), str(refused.exception))
				self.assertIn(meaning, str(refused.exception))

	def test_an_unmapped_meta_error_still_rides_the_backoff(self):
		"""131000 is documented 'unknown error -- try again': transient."""
		with self.assertRaises(frappe.ValidationError) as raised:
			self._send_with(_Response(400, _meta_error(131000)))
		self.assertNotIsInstance(raised.exception, PermanentRejection)

	def test_a_mark_read_failure_is_not_a_send_refusal(self):
		"""`_post` serves mark_read too; its errors must never be classified
		as a refused message -- a failed read-receipt is not terminal for a
		queue row."""
		with (
			patch(
				"relay.integrations.meta_cloud_api.requests.post",
				return_value=_Response(400, _meta_error(131047)),
			),
			patch("frappe.log_error"),
		):
			with self.assertRaises(frappe.ValidationError) as raised:
				self.adapter.mark_read("wamid.fake")
		self.assertNotIsInstance(raised.exception, PermanentRejection)
