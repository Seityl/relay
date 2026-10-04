# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""A webhook that cannot be verified is refused, not accepted (#12).

`receive` is relay's only `allow_guest=True` endpoint. The signature check
used to run only when a signature header was PRESENT, and Meta's adapter
kept the base default `requires_valid_signature() -> False` -- so an
unsigned request on an account that HAS an app secret was accepted (200,
message saved) while a bad signature got 401. Verification by omission.

The same review found the no-secret paths crash instead of refusing: a
real Relay Account without `app_secret` makes `get_password` throw inside
`validate_webhook_signature` (500), and Twilio's
`requires_valid_signature` called `get_access_token()` -- which throws on
an account without an auth token -- so the "unsigned is refused when we
could have checked" intent was a 500 in exactly the configuration it was
written for (#13's item 2).

Decisions recorded here (the issue asked for an explicit decision):
- When the signing secret IS configured, an unsigned request is refused
  401 "Missing webhook signature".
- When it is NOT configured, a signed request is refused 401 naming the
  account -- fail closed, with the real cause in the refusal, rather than
  the old allow-through-with-a-warning (fail open) or the 500.
- An unsigned request on a secretless account is still refused: there is
  no configuration in which accepting it is right for a channel that has
  a signature scheme.

Fixtures are per-test: the no-secret drives reach `_handle_post`'s except
and roll back the transaction, which would wipe class fixtures.
"""

import hashlib
import hmac
import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from relay.webhooks.handler import _handle_post

APP_SECRET = "sigreq-test-app-secret"
ACCOUNT = "Meta-SigReq-Test"
ACCOUNT_NO_SECRET = "Meta-SigReq-NoSecret-Test"
TWILIO_NO_TOKEN = "Twilio-SigReq-NoToken-Test"
CHANNEL = "Meta SigReq Test Channel"
TWILIO_CHANNEL = "Twilio SigReq Test Channel"
ORIGIN = "https://relay.test"
PATH = "/api/method/relay.webhooks.handler.receive"
PHONE = "15550009999"
PHONE_ID = "sigreq-test-phone-id"
PHONE_ID_NO_SECRET = "sigreq-no-secret-phone-id"


class _Headers(dict):
	def get(self, key, default=""):
		for k, v in self.items():
			if k.lower() == key.lower():
				return v
		return default


class _Form(dict):
	def to_dict(self):
		return dict(self)


class _JsonRequest:
	method = "POST"

	def __init__(self, body: bytes, headers: dict | None = None, account: str = ACCOUNT):
		self.data = body
		self.headers = _Headers(headers or {})
		self._account = account
		self.url = f"{ORIGIN}{PATH}?account={account}"
		self.path = PATH
		self.query_string = f"account={account}".encode()

	@property
	def form(self):
		return _Form({})

	@property
	def args(self):
		return _Form({"account": self._account})


class TestTheSignatureRequirement(IntegrationTestCase):
	def setUp(self):
		super().setUp()

		if not frappe.db.exists("Relay Channel", CHANNEL):
			frappe.get_doc(
				{
					"doctype": "Relay Channel",
					"channel_name": CHANNEL,
					"provider": "Meta Cloud API",
					"enabled": 1,
				}
			).insert(ignore_permissions=True)
		if not frappe.db.exists("Relay Channel", TWILIO_CHANNEL):
			frappe.get_doc(
				{
					"doctype": "Relay Channel",
					"channel_name": TWILIO_CHANNEL,
					"provider": "Twilio",
					"enabled": 1,
				}
			).insert(ignore_permissions=True)

		# The #12 target: a Meta account WITH an app secret.
		if not frappe.db.exists("Relay Account", {"account_name": ACCOUNT}):
			frappe.get_doc(
				{
					"doctype": "Relay Account",
					"account_name": ACCOUNT,
					"status": "Active",
					"channel": CHANNEL,
					"app_secret": APP_SECRET,
					"access_token": "sigreq-test-access-token",
					"phone_number_id": PHONE_ID,
					"api_url": "https://graph.facebook.com",
					"api_version": "v21.0",
				}
			).insert(ignore_permissions=True)

		# A real Meta account with NO app secret (the configuration the old
		# allow-through test pretended existed).
		if not frappe.db.exists("Relay Account", {"account_name": ACCOUNT_NO_SECRET}):
			frappe.get_doc(
				{
					"doctype": "Relay Account",
					"account_name": ACCOUNT_NO_SECRET,
					"status": "Active",
					"channel": CHANNEL,
					"access_token": "sigreq-test-access-token",
					"phone_number_id": PHONE_ID_NO_SECRET,
					"api_url": "https://graph.facebook.com",
					"api_version": "v21.0",
				}
			).insert(ignore_permissions=True)

		# A real Twilio account with NO auth token (#13 item 2).
		if not frappe.db.exists("Relay Account", {"account_name": TWILIO_NO_TOKEN}):
			frappe.get_doc(
				{
					"doctype": "Relay Account",
					"account_name": TWILIO_NO_TOKEN,
					"status": "Active",
					"channel": TWILIO_CHANNEL,
					"credentials": json.dumps(
						{"account_sid": "AC00000000000000000000000000000000"}
					),
				}
			).insert(ignore_permissions=True)

	def _post_meta(self, account: str, body: bytes, headers: dict | None = None):
		"""Drive the webhook with the request faked at frappe.request.

		`frappe.db.commit` is suppressed (the handler commits in several
		places; a real commit would defeat the class rollback -- see
		test_signature_gate.py's _post). `frappe.log_error` is captured:
		it commits, which would poison the suite's open transaction. The
		media-free text payload means the download seam is never reached.
		"""
		request = _JsonRequest(body, headers, account)
		with (
			patch("frappe.request", request),
			patch("frappe.db.commit"),
			patch("frappe.utils.get_url", return_value=ORIGIN),
			patch("frappe.log_error") as log_error,
		):
			frappe.local.form_dict = frappe._dict()
			try:
				response = _handle_post()
			finally:
				frappe.local.form_dict = frappe._dict()
		self.log_error = log_error
		return response

	def _meta_body(self) -> bytes:
		payload = {
			"entry": [
				{
					"changes": [
						{
							"field": "messages",
							"value": {
								"metadata": {"phone_number_id": PHONE_ID},
								"contacts": [
									{"wa_id": PHONE, "profile": {"name": "sigreq test patient"}}
								],
								"messages": [
									{
										"from": PHONE,
										"id": f"wamid.{frappe.generate_hash(length=10)}",
										"type": "text",
										"text": {"body": "signature requirement probe"},
									}
								],
							},
						}
					]
				}
			]
		}
		return json.dumps(payload).encode()

	def _meta_signature(self, body: bytes) -> str:
		return "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()

	def _webhook_log_reason(self, account: str):
		return frappe.db.get_value(
			"Relay Webhook Log",
			{"account": account},
			["name", "status", "error_log"],
			as_dict=True,
			order_by="creation desc",
		)

	def test_an_unsigned_meta_webhook_is_refused_when_the_account_has_an_app_secret(self):
		"""#12, the headline: the signature check ran only when a signature
		was present, so omitting the header bypassed verification on an
		account that HAS the key to verify with."""
		body = self._meta_body()

		response = self._post_meta(ACCOUNT, body, headers={})

		self.assertEqual(
			response.status_code,
			401,
			"an unsigned webhook was accepted on an account with an app secret; "
			"omitting the header is enough to bypass verification on a "
			"guest-callable endpoint",
		)
		row = self._webhook_log_reason(ACCOUNT)
		self.assertTrue(row, "the refusal was not recorded")
		self.assertEqual(row.status, "Failed")
		self.assertEqual(row.error_log, "Missing webhook signature")

	def test_a_signed_webhook_without_a_signing_secret_is_refused_naming_the_account(self):
		"""The no-secret configuration used to answer a 500: get_password
		threw inside validate. It must be a refusal that names the account,
		so the operator can fix the configuration instead of reading a
		traceback (#12's 'decide explicitly', #13 item 2's shape)."""
		body = self._meta_body()

		response = self._post_meta(
			ACCOUNT_NO_SECRET, body, headers={"X-Hub-Signature-256": "sha256=anything"}
		)

		self.assertEqual(response.status_code, 401)
		self.assertIn(
			ACCOUNT_NO_SECRET,
			response.get_data(as_text=True),
			"the refusal does not name the account whose secret is missing",
		)
		self.assertIn("no signing secret", response.get_data(as_text=True))
		row = self._webhook_log_reason(ACCOUNT_NO_SECRET)
		self.assertTrue(row, "the refusal was not recorded")
		self.assertIn(ACCOUNT_NO_SECRET, row.error_log or "")

	def test_an_unsigned_webhook_is_refused_even_when_no_signing_secret_is_configured(self):
		"""The unsigned refusal must not depend on the secret: there is no
		configuration in which accepting an unsigned webhook is right for a
		channel that has a signature scheme."""
		body = self._meta_body()

		response = self._post_meta(ACCOUNT_NO_SECRET, body, headers={})

		self.assertEqual(response.status_code, 401)
		row = self._webhook_log_reason(ACCOUNT_NO_SECRET)
		self.assertTrue(row, "the refusal was not recorded")
		self.assertEqual(row.error_log, "Missing webhook signature")

	def test_a_signed_webhook_with_a_valid_signature_is_accepted(self):
		"""The control: without it, the refusals above would also pass if
		the gate refused everything -- which would be a different outage."""
		body = self._meta_body()

		response = self._post_meta(
			ACCOUNT, body, headers={"X-Hub-Signature-256": self._meta_signature(body)}
		)

		self.assertEqual(
			response.status_code,
			200,
			"a correctly signed webhook was refused, so the channel accepts nothing",
		)

	def test_a_twilio_webhook_without_an_auth_token_is_refused_not_500(self):
		"""#13 item 2: Twilio's requires_valid_signature read the auth token
		and threw on an account without one -- a 500 (which Twilio retries)
		in exactly the configuration the refusal was written for."""
		body = self._meta_body()

		response = self._post_meta(TWILIO_NO_TOKEN, body, headers={})

		self.assertEqual(response.status_code, 401)
		row = self._webhook_log_reason(TWILIO_NO_TOKEN)
		self.assertTrue(row, "the refusal was not recorded")
		self.assertEqual(row.error_log, "Missing webhook signature")

	def test_a_signed_twilio_webhook_without_an_auth_token_names_the_account(self):
		"""#13 item 2, signed shape: the same account with a signature
		header must get the named refusal, not the get_password 500."""
		body = self._meta_body()

		response = self._post_meta(
			TWILIO_NO_TOKEN, body, headers={"X-Twilio-Signature": "anything"}
		)

		self.assertEqual(response.status_code, 401)
		self.assertIn(
			TWILIO_NO_TOKEN,
			response.get_data(as_text=True),
			"the refusal does not name the account whose token is missing",
		)
		row = self._webhook_log_reason(TWILIO_NO_TOKEN)
		self.assertTrue(row, "the refusal was not recorded")
		self.assertIn(TWILIO_NO_TOKEN, row.error_log or "")
