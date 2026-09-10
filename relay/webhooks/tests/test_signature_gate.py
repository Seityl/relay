# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""An unsigned webhook does not get in by being unsigned (#5).

`receive` is the app's only `allow_guest=True` endpoint. It creates
Contacts, Threads and Messages, and it runs the intent engine, which can
send an automated reply. Anything that can post to it can drive all of that.

The gate had two holes, and the second is the one that matters:

  1. The signature header name was hardcoded to Meta's
     `X-Hub-Signature-256`, so no other provider's signature was ever read.

  2. The check was `if signature and not adapter.validate(...)`. A request
     with no signature header skipped verification entirely -- so for any
     provider, omitting the header was sufficient to bypass it. Hole 1 fed
     hole 2: a Twilio request would always arrive "unsigned" as far as the
     handler could tell, and therefore always be accepted.

These tests drive `_handle_post` the way a provider does, rather than
asserting on the helpers, because the bug lived in how the helpers were
combined and not in any one of them.
"""

import base64
import hashlib
import hmac
import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from relay.webhooks.handler import _handle_post, _signature_from

AUTH_TOKEN = "gate-test-auth-token"
ACCOUNT = "Twilio-Gate-Test"
CHANNEL = "Twilio Gate Test Channel"
ORIGIN = "https://relay.test"
PATH = "/api/method/relay.webhooks.handler.receive"
QUERY = f"account={ACCOUNT}"
#: What Twilio signs: the callback URL as configured, which is what
#: `_signed_url` rebuilds from the site origin. The URL the request arrives
#: with is deliberately different below, because here it always is.
URL = f"{ORIGIN}{PATH}?{QUERY}"


class _Headers(dict):
	def get(self, key, default=""):
		for k, v in self.items():
			if k.lower() == key.lower():
				return v
		return default


class _Form(dict):
	def to_dict(self):
		return dict(self)


class _Request:
	method = "POST"

	def __init__(self, form, headers):
		self._form = form
		self.headers = _Headers(headers)
		# As the proxy delivers it: host rewritten to the backend, scheme
		# reported as http. Nothing like the URL Twilio signed.
		self.url = f"http://10.6.0.35{PATH}?{QUERY}"
		self.path = PATH
		self.query_string = QUERY.encode()
		self.data = b""

	@property
	def form(self):
		return _Form(self._form)

	@property
	def args(self):
		return _Form({"account": ACCOUNT})


def _sign(url: str, params: dict) -> str:
	payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
	return base64.b64encode(
		hmac.new(AUTH_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()
	).decode()


class TestSignatureGate(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		if not frappe.db.exists("Relay Channel", CHANNEL):
			frappe.get_doc(
				{
					"doctype": "Relay Channel",
					"channel_name": CHANNEL,
					"provider": "Twilio",
					"enabled": 1,
				}
			).insert(ignore_permissions=True)

		account = frappe.get_doc(
			{
				"doctype": "Relay Account",
				"account_name": ACCOUNT,
				"status": "Active",
				"channel": CHANNEL,
				"identifier_type": "Phone",
				"identifier_value": "15550002222",
				"credentials": json.dumps({"account_sid": "AC00000000000000000000000000000000"}),
			}
		)
		account.access_token = AUTH_TOKEN
		account.insert(ignore_permissions=True)
		cls.account = account

	def _post(self, params, headers):
		"""Drive the handler as a provider does, without letting it commit.

		`_handle_post` commits in three places -- after logging, after
		persisting a message, and when refusing one. A commit inside a test
		commits the *test's* open transaction too, so `IntegrationTestCase`
		has nothing left to roll back.

		That is not hypothetical: the first run of this file left a Relay
		Account, a Relay Channel, five Webhook Logs, two Messages and a
		Thread on the site, which had to be removed by document ID. The
		fixtures are still rolled back at class teardown; suppressing the
		commit is what makes that possible.
		"""
		request = _Request(params, headers)
		with (
			patch("frappe.request", request),
			patch("frappe.db.commit"),
			patch("frappe.utils.get_url", return_value=ORIGIN),
		):
			frappe.local.form_dict = frappe._dict(params)
			try:
				return _handle_post()
			finally:
				frappe.local.form_dict = frappe._dict()

	# --- the hole ---------------------------------------------------------

	def test_an_unsigned_request_is_refused(self):
		"""The whole point.

		Before the fix this returned 200 and processed the payload, because
		no signature meant no check.
		"""
		params = {"MessageSid": "SM-unsigned", "WaId": "15550001111", "Body": "let me in"}

		response = self._post(params, headers={})

		self.assertEqual(
			response.status_code,
			401,
			"an unsigned webhook was accepted; omitting the header is enough "
			"to bypass verification on a guest-callable endpoint",
		)

	def test_a_request_signed_with_the_wrong_key_is_refused(self):
		params = {"MessageSid": "SM-bad", "WaId": "15550001111", "Body": "hi"}
		forged = base64.b64encode(
			hmac.new(b"not-the-token", (URL + "Body" + "hi").encode(), hashlib.sha1).digest()
		).decode()

		response = self._post(params, headers={"X-Twilio-Signature": forged})

		self.assertEqual(response.status_code, 401)

	def test_a_properly_signed_request_is_accepted(self):
		"""The control.

		Without it, the two tests above would also pass if the gate rejected
		everything -- which would be a different outage, not a fix.
		"""
		params = {"MessageSid": "SM-good", "WaId": "15550001111", "Body": "hello"}

		response = self._post(params, headers={"X-Twilio-Signature": _sign(URL, params)})

		self.assertEqual(
			response.status_code,
			200,
			"a correctly signed webhook was refused, so the channel accepts nothing",
		)

	def test_the_signature_is_read_from_the_providers_own_header(self):
		"""Meta's header must not satisfy a Twilio account.

		The handler used to read only `X-Hub-Signature-256`. Sending a valid
		Twilio signature in Meta's header should not get in, and -- more to
		the point -- a Twilio signature in Twilio's header should.
		"""
		params = {"MessageSid": "SM-hdr", "WaId": "15550001111", "Body": "hello"}
		signature = _sign(URL, params)

		wrong_header = self._post(params, headers={"X-Hub-Signature-256": signature})
		self.assertEqual(wrong_header.status_code, 401)

		right_header = self._post(params, headers={"X-Twilio-Signature": signature})
		self.assertEqual(right_header.status_code, 200)

	# --- header casing ----------------------------------------------------

	def test_a_signature_header_is_found_whatever_the_proxy_cased_it(self):
		"""HTTP header names are case-insensitive; proxies vary.

		Looking up one exact spelling is how a signature that was sent reads
		as absent -- and an absent signature used to mean no check at all.
		"""
		for spelling in ("X-Twilio-Signature", "x-twilio-signature", "X-TWILIO-SIGNATURE"):
			self.assertEqual(
				_signature_from({spelling: "abc"}, "X-Twilio-Signature"),
				"abc",
				f"a signature sent as {spelling!r} was not found",
			)

		self.assertEqual(
			_signature_from({"X-Something-Else": "abc"}, "X-Twilio-Signature"),
			"",
			"an unrelated header was read as a signature",
		)
