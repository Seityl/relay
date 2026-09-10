# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""The Twilio adapter speaks Twilio and stores Relay (#5).

Every test here is about a place where Twilio's shape and relay's shape
disagree, because those are the places a channel adapter goes wrong quietly:
an address that carries a scheme, a status vocabulary that is not relay's, a
signature computed over something other than the body, and one endpoint
serving two kinds of event.
"""

import base64
import hashlib
import hmac
import json
import pathlib
import unittest
from unittest.mock import patch

from relay.integrations.twilio_adapter import STATUS_MAP, TwilioAdapter

AUTH_TOKEN = "test-auth-token"

APP_ROOT = pathlib.Path(__file__).resolve().parents[2]
MESSAGE_JSON = APP_ROOT / "relay" / "doctype" / "relay_message" / "relay_message.json"


class _Account:
	"""The few attributes the adapter reads off a Relay Account."""

	name = "Twilio Test Account"
	identifier_value = "15550002222"
	credentials = json.dumps({"account_sid": "AC00000000000000000000000000000000"})

	def get_access_token(self):
		return AUTH_TOKEN


class _Request:
	def __init__(self, url, form, headers=None):
		self.url = url
		self._form = form
		self.headers = headers or {}

	@property
	def form(self):
		class _Form(dict):
			def to_dict(self):
				return dict(self)

		return _Form(self._form)


def _relay_message_statuses() -> set:
	"""The Select options, read from the DocType rather than from memory."""
	data = json.loads(MESSAGE_JSON.read_text())
	for field in data["fields"]:
		if field["fieldname"] == "status":
			return set(field["options"].split("\n"))
	raise AssertionError("Relay Message has no status field any more")


class TestTwilioAdapter(unittest.TestCase):
	def setUp(self):
		self.adapter = TwilioAdapter(_Account())

	# --- addresses -------------------------------------------------------

	def test_a_whatsapp_address_loses_its_scheme_and_its_plus(self):
		"""Relay stores identifiers bare.

		If `whatsapp:+1767...` reached `RelayContact.get_or_create`, the
		lookup would miss an existing `1767...` identifier and quietly build
		a second Contact for the same person.
		"""
		self.assertEqual(self.adapter.normalize_identifier("Phone", "whatsapp:+15550002222"), "15550002222")
		self.assertEqual(self.adapter.normalize_identifier("Phone", "+15550002222"), "15550002222")
		self.assertEqual(self.adapter.normalize_identifier("Phone", " 15550002222 "), "15550002222")

	def test_an_inbound_message_is_identified_by_its_bare_whatsapp_id(self):
		"""Twilio sends both `WaId` and `From`; only one is already bare."""
		payload = {
			"MessageSid": "SM123",
			"From": "whatsapp:+15550002222",
			"WaId": "15550002222",
			"Body": "hello",
			"ProfileName": "Jeriel",
			"NumMedia": "0",
		}

		message = self.adapter.parse_inbound_webhook(payload).messages[0]

		self.assertEqual(message.from_identifier.identifier_value, "15550002222")
		self.assertEqual(message.from_identifier.identifier_type, "Phone")
		self.assertEqual(message.from_identifier.display_name, "Jeriel")
		self.assertEqual(message.body, "hello")
		self.assertEqual(message.provider_message_id, "SM123")

	# --- one endpoint, two kinds of event --------------------------------

	def test_a_delivery_receipt_is_not_read_as_a_new_message(self):
		"""Both arrive at the same URL. `MessageStatus` is the only tell.

		Read as a message, a delivery receipt would create an inbound
		`Relay Message` with an empty body from the recipient, and run the
		intent engine and auto-reply rules against it.
		"""
		payload = {"MessageSid": "SM123", "MessageStatus": "delivered", "To": "whatsapp:+15550002222"}

		normalized = self.adapter.parse_inbound_webhook(payload)

		self.assertEqual(normalized.messages, [])
		self.assertEqual(len(normalized.status_events), 1)
		self.assertEqual(normalized.status_events[0].provider_message_id, "SM123")
		self.assertEqual(normalized.status_events[0].status, "Delivered")

	def test_a_status_event_arrives_already_translated(self):
		"""`webhooks/handler.py` never calls map_status itself.

		`_persist_status_event` branches on the literals Sent/Delivered/Read/
		Failed and writes `event.status` straight onto the document, so an
		untranslated `delivered` would be written into a Select that has no
		such option.
		"""
		payload = {"MessageSid": "SM1", "MessageStatus": "read"}
		event = self.adapter.parse_inbound_webhook(payload).status_events[0]

		self.assertEqual(event.status, "Read")
		self.assertNotEqual(event.status, "read")

	# --- the status vocabulary -------------------------------------------

	def test_every_mapped_status_is_one_relay_can_store(self):
		"""A Select field validates on save.

		Writing a value outside its options raises ValidationError mid-flight
		-- in a scheduled job that rolls back the whole transaction, taking
		unrelated work with it. So the map may only ever produce options that
		exist on the DocType, read here from the JSON rather than from
		memory.
		"""
		allowed = _relay_message_statuses()
		produced = set(STATUS_MAP.values())

		self.assertTrue(
			produced <= allowed,
			f"these map to statuses Relay Message cannot store: {sorted(produced - allowed)}",
		)

	def test_an_undelivered_message_is_a_failure_not_a_pending_state(self):
		"""Twilio's `undelivered` is terminal.

		Passing it through unmapped would leave the message looking like it
		was still on its way, for ever.
		"""
		self.assertEqual(self.adapter.map_status("undelivered"), "Failed")
		self.assertEqual(self.adapter.map_status("failed"), "Failed")

	def test_an_unknown_status_is_passed_through_rather_than_guessed(self):
		self.assertEqual(self.adapter.map_status("something-new"), "something-new")

	# --- the capability declaration --------------------------------------

	def test_read_receipts_are_not_claimed(self):
		"""`handler.py:299` calls `adapter.mark_read()` on anything claiming it.

		`mark_read` is not declared on BaseChannelAdapter and is not defined
		here, so claiming the feature would raise AttributeError into a
		swallowed `frappe.log_error` on every inbound message.
		"""
		self.assertFalse(self.adapter.supports("read_receipts"))
		self.assertFalse(
			hasattr(self.adapter, "mark_read"),
			"mark_read now exists, so supports('read_receipts') may be reconsidered",
		)

	def test_the_features_that_are_claimed_are_implemented(self):
		for feature in ("templates", "media", "webhooks", "status_callbacks"):
			self.assertTrue(self.adapter.supports(feature), feature)

	# --- signatures ------------------------------------------------------

	def _sign(self, url, params):
		payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
		return base64.b64encode(
			hmac.new(AUTH_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()
		).decode()

	def test_a_signature_over_the_url_and_sorted_params_is_accepted(self):
		"""The documented Twilio algorithm, reproduced independently here."""
		url = "https://rxflow-dev.jollys.dm/api/method/relay.webhooks.handler.receive?account=T"
		params = {"MessageSid": "SM1", "Body": "hi", "From": "whatsapp:+1767"}

		with patch("frappe.request", _Request(url, params)):
			self.assertTrue(self.adapter.validate_webhook_signature(b"", self._sign(url, params)))

	def test_a_signature_computed_over_a_different_url_is_rejected(self):
		"""The URL is part of what is signed, which is the point.

		Meta signs the body alone; if this adapter did the same, a payload
		captured from one endpoint would validate against another.
		"""
		url = "https://rxflow-dev.jollys.dm/api/method/relay.webhooks.handler.receive?account=T"
		params = {"MessageSid": "SM1", "Body": "hi"}
		signature = self._sign("https://evil.example/receive", params)

		with patch("frappe.request", _Request(url, params)):
			self.assertFalse(self.adapter.validate_webhook_signature(b"", signature))

	def test_a_tampered_parameter_is_rejected(self):
		url = "https://rxflow-dev.jollys.dm/api/method/relay.webhooks.handler.receive"
		signature = self._sign(url, {"MessageSid": "SM1", "Body": "hi"})

		with patch("frappe.request", _Request(url, {"MessageSid": "SM1", "Body": "tampered"})):
			self.assertFalse(self.adapter.validate_webhook_signature(b"", signature))

	def test_the_adapter_names_the_header_its_signature_arrives_in(self):
		"""The shared handler hardcoded Meta's header.

		Any other provider's signature was therefore never read -- and since
		the handler skips verification when it finds none, never checked.
		"""
		self.assertEqual(self.adapter.signature_header(), "X-Twilio-Signature")

	def test_an_unsigned_request_is_refused_for_this_channel(self):
		"""The Auth Token is both the API credential and the signing key.

		If sending works at all, verification is possible, so there is no
		configuration in which an unsigned webhook should be accepted.
		"""
		self.assertTrue(self.adapter.requires_valid_signature())

	def test_a_signature_still_matches_behind_a_tls_terminating_proxy(self):
		"""Twilio signs the public https URL, not the one gunicorn sees.

		gunicorn serves plain HTTP behind a proxy here, so `request.url`
		reports `http://`. Without honouring X-Forwarded-Proto every
		signature fails to match, and the failure looks like a bad token
		rather than a scheme mismatch.
		"""
		public = "https://rxflow-dev.jollys.dm/api/method/relay.webhooks.handler.receive?account=T"
		as_received = public.replace("https://", "http://", 1)
		params = {"MessageSid": "SM1", "Body": "hi"}

		request = _Request(as_received, params, headers={"X-Forwarded-Proto": "https"})
		with patch("frappe.request", request):
			self.assertTrue(
				self.adapter.validate_webhook_signature(b"", self._sign(public, params)),
				"a signature Twilio computed over the https URL was rejected because "
				"gunicorn reported the request as http",
			)

	def test_a_missing_signature_is_rejected_rather_than_waved_through(self):
		"""Deliberately unlike the Meta adapter.

		`MetaCloudAPIAdapter.validate_webhook_signature` returns True when no
		app secret is configured, and `handler.py:85` skips the check
		entirely when the header is absent. On a guest-callable endpoint that
		is a hole; this adapter does not widen it.
		"""
		url = "https://rxflow-dev.jollys.dm/api/method/relay.webhooks.handler.receive"
		with patch("frappe.request", _Request(url, {"MessageSid": "SM1"})):
			self.assertFalse(self.adapter.validate_webhook_signature(b"", ""))

	# --- media -----------------------------------------------------------

	def test_inbound_media_becomes_an_attachment_with_its_relay_content_type(self):
		payload = {
			"MessageSid": "SM9",
			"WaId": "15550002222",
			"NumMedia": "1",
			"MediaUrl0": "https://api.twilio.com/media/ME1",
			"MediaContentType0": "image/jpeg",
			"Body": "",
		}

		with patch.object(TwilioAdapter, "_download", return_value=b"bytes"):
			message = self.adapter.parse_inbound_webhook(payload).messages[0]

		self.assertEqual(message.content_type, "image")
		self.assertEqual(len(message.attachments), 1)
		self.assertEqual(message.attachments[0].mime_type, "image/jpeg")
		self.assertEqual(message.attachments[0].content, b"bytes")

	def test_a_mime_type_relay_has_no_option_for_is_stored_as_a_document(self):
		"""`content_type` is a Select too, so this cannot be passed through."""
		payload = {
			"MessageSid": "SM9",
			"WaId": "1767",
			"NumMedia": "1",
			"MediaUrl0": "https://api.twilio.com/media/ME1",
			"MediaContentType0": "application/vnd.oasis.opendocument.text",
		}

		with patch.object(TwilioAdapter, "_download", return_value=b""):
			message = self.adapter.parse_inbound_webhook(payload).messages[0]

		self.assertEqual(message.content_type, "document")
