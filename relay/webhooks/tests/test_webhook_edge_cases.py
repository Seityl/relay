# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Webhook edge cases from the #10 review (#13): a status Relay cannot
store, and a webhook that resolves to no account.

- An unmappable provider status used to pass through `map_status`
  unchanged into a Select that has no such option: the save raised
  ValidationError mid-webhook, the handler's rollback threw the whole
  batch away, and the provider retried into the same refusal. The parser
  now skips the event and names it; the webhook answers 200 and the rest
  of the batch lands.
- A webhook whose account cannot be resolved used to take the
  empty-payload branch and answer **200 OK** -- the provider treats that
  as delivered and never retries, so the message is silently lost. It is
  now refused 400, naming what to configure.

These tests drive `_handle_post` the way the providers do: Twilio as a
signed form POST, Meta as a signed JSON POST. Fixtures are per-test (the
pre-fix drives reach the handler's rollback).
"""

import base64
import hashlib
import hmac
import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from relay.webhooks.handler import _handle_post

META_SECRET = "edge-case-meta-secret"
META_ACCOUNT = "Meta-EdgeCase-Test"
TWILIO_ACCOUNT = "Twilio-EdgeCase-Test"
META_CHANNEL = "Meta EdgeCase Test Channel"
TWILIO_CHANNEL = "Twilio EdgeCase Test Channel"
ORIGIN = "https://relay.test"
PATH = "/api/method/relay.webhooks.handler.receive"
PHONE = "15550001234"
PHONE_ID = "edgecase-meta-phone-id"


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

	def __init__(self, body: bytes, headers: dict | None = None, account: str | None = None):
		self.data = body
		self.headers = _Headers(headers or {})
		self._account = account
		query = f"account={account}" if account else ""
		self.url = f"{ORIGIN}{PATH}" + (f"?{query}" if query else "")
		self.path = PATH
		self.query_string = query.encode()

	@property
	def form(self):
		return _Form({})

	@property
	def args(self):
		return _Form({"account": self._account} if self._account else {})


class _FormRequest:
	method = "POST"

	def __init__(self, form: dict, headers: dict, account: str):
		self._form = form
		self._account = account
		self.headers = _Headers(headers)
		self.data = b""
		self.url = f"{ORIGIN}{PATH}?account={account}"
		self.path = PATH
		self.query_string = f"account={account}".encode()

	@property
	def form(self):
		return _Form(self._form)

	@property
	def args(self):
		return _Form({"account": self._account})


def _twilio_signature(url: str, params: dict, token: str) -> str:
	payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
	return base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()


class TestAnUnstorableStatus(IntegrationTestCase):
	def setUp(self):
		super().setUp()

		if not frappe.db.exists("Relay Channel", META_CHANNEL):
			frappe.get_doc(
				{
					"doctype": "Relay Channel",
					"channel_name": META_CHANNEL,
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

		if not frappe.db.exists("Relay Account", {"account_name": META_ACCOUNT}):
			frappe.get_doc(
				{
					"doctype": "Relay Account",
					"account_name": META_ACCOUNT,
					"status": "Active",
					"channel": META_CHANNEL,
					"app_secret": META_SECRET,
					"access_token": "edgecase-meta-access-token",
					"phone_number_id": PHONE_ID,
					"api_url": "https://graph.facebook.com",
					"api_version": "v21.0",
				}
			).insert(ignore_permissions=True)
		if not frappe.db.exists("Relay Account", {"account_name": TWILIO_ACCOUNT}):
			frappe.get_doc(
				{
					"doctype": "Relay Account",
					"account_name": TWILIO_ACCOUNT,
					"status": "Active",
					"channel": TWILIO_CHANNEL,
					"access_token": "edgecase-twilio-auth-token",
					"credentials": json.dumps(
						{"account_sid": "AC00000000000000000000000000000000"}
					),
				}
			).insert(ignore_permissions=True)

	def _drive(self, request, form_dict: dict | None = None):
		"""Run the handler with the request faked; commits suppressed
		(the handler commits in several places), log_error captured (it
		commits -- the side-row lesson). form_dict carries the payload for
		form-encoded providers (the handler reads frappe.local.form_dict,
		not request.form; the gate's _post does the same) and stays empty
		for JSON bodies so the handler parses request.data."""
		with (
			patch("frappe.request", request),
			patch("frappe.db.commit"),
			patch("frappe.utils.get_url", return_value=ORIGIN),
			patch("frappe.log_error") as log_error,
		):
			frappe.local.form_dict = frappe._dict(form_dict or {})
			try:
				response = _handle_post()
			finally:
				frappe.local.form_dict = frappe._dict()
		self.log_error = log_error
		return response

	def _post_meta(self, account: str, payload: dict, headers: dict | None = None):
		body = json.dumps(payload).encode()
		signature = "sha256=" + hmac.new(META_SECRET.encode(), body, hashlib.sha256).hexdigest()
		request = _JsonRequest(body, {**(headers or {}), "X-Hub-Signature-256": signature}, account)
		return self._drive(request)

	def _post_twilio(self, account: str, params: dict):
		token = "edgecase-twilio-auth-token"
		url = f"{ORIGIN}{PATH}?account={account}"
		signature = _twilio_signature(url, params, token)
		request = _FormRequest(params, {"X-Twilio-Signature": signature}, account)
		return self._drive(request, form_dict=params)

	def _meta_inbound(self, media_id: str) -> dict:
		return {
			"entry": [
				{
					"changes": [
						{
							"field": "messages",
							"value": {
								"metadata": {"phone_number_id": PHONE_ID},
								"contacts": [
									{"wa_id": PHONE, "profile": {"name": "edge case patient"}}
								],
								"messages": [
									{
										"from": PHONE,
										"id": f"wamid.{media_id}",
										"type": "text",
										"text": {"body": "edge case probe"},
									}
								],
							},
						}
					]
				}
			]
		}

	def test_an_unstorable_twilio_status_does_not_kill_the_webhook(self):
		"""#13 item 1: `partially_delivered`-class statuses passed through
		map_status into a Select that has no such option; the save raised
		ValidationError and the webhook 500'd -- Twilio retried, every
		event in the payload was lost with it. The parser now skips the
		unstorable status, names it, and the webhook answers 200."""
		sid = f"SM{frappe.generate_hash(length=10)}"
		first = self._post_twilio(
			TWILIO_ACCOUNT,
			{
				"MessageSid": sid,
				"From": "whatsapp:+15550001234",
				"WaId": "15550001234",
				"Body": "edge case probe",
				"NumMedia": "0",
			},
		)
		self.assertEqual(first.status_code, 200, "the inbound did not land")
		name = frappe.db.get_value("Relay Message", {"message_id": sid, "direction": "Incoming"}, "name")
		self.assertTrue(name, "the inbound message did not persist")
		status_before = frappe.db.get_value("Relay Message", name, "status")

		second = self._post_twilio(TWILIO_ACCOUNT, {"MessageSid": sid, "MessageStatus": "something-new"})

		self.assertEqual(second.status_code, 200, "an unstorable status 500'd the webhook")
		self.assertEqual(
			frappe.db.get_value("Relay Message", name, "status"),
			status_before,
			"the message's status was changed by a receipt Relay cannot store",
		)
		named = [
			c
			for c in self.log_error.call_args_list
			if sid in str(c.kwargs.get("title", "")) and "something-new" in str(c.kwargs.get("title", ""))
		]
		self.assertTrue(named, f"the unstorable status was not named: {self.log_error.call_args_list}")

	def test_an_unstorable_meta_status_does_not_kill_the_webhook(self):
		"""The same defect on Meta's adapter, same shape."""
		media_id = frappe.generate_hash(length=10)
		first = self._post_meta(META_ACCOUNT, self._meta_inbound(media_id))
		self.assertEqual(first.status_code, 200, "the inbound did not land")
		name = frappe.db.get_value(
			"Relay Message", {"message_id": f"wamid.{media_id}", "direction": "Incoming"}, "name"
		)
		self.assertTrue(name, "the inbound message did not persist")
		status_before = frappe.db.get_value("Relay Message", name, "status")

		second = self._post_meta(
			META_ACCOUNT,
			{
				"entry": [
					{
						"changes": [
							{
								"field": "messages",
								"value": {
									"metadata": {"phone_number_id": PHONE_ID},
									"statuses": [{"id": f"wamid.{media_id}", "status": "something-new"}],
								},
							}
						]
					}
				]
			},
		)

		self.assertEqual(second.status_code, 200, "an unstorable status 500'd the webhook")
		self.assertEqual(
			frappe.db.get_value("Relay Message", name, "status"),
			status_before,
			"the message's status was changed by a receipt Relay cannot store",
		)
		named = [
			c
			for c in self.log_error.call_args_list
			if media_id in str(c.kwargs.get("title", "")) and "something-new" in str(c.kwargs.get("title", ""))
		]
		self.assertTrue(named, f"the unstorable status was not named: {self.log_error.call_args_list}")


class TestAnUnresolvableWebhook(IntegrationTestCase):
	def setUp(self):
		super().setUp()

		if not frappe.db.exists("Relay Channel", META_CHANNEL):
			frappe.get_doc(
				{
					"doctype": "Relay Channel",
					"channel_name": META_CHANNEL,
					"provider": "Meta Cloud API",
					"enabled": 1,
				}
			).insert(ignore_permissions=True)

		# The refusal must be about THIS payload, not about whatever the
		# site's seed left flagged: no account is the default incoming
		# target for the duration of the test (in-transaction; the class
		# rollback restores it).
		for flagged in frappe.get_all(
			"Relay Account", filters={"is_default_incoming": 1}, pluck="name"
		):
			frappe.db.set_value("Relay Account", flagged, "is_default_incoming", 0)

	def test_a_webhook_that_resolves_no_account_is_refused_not_silently_dropped(self):
		"""#13 item 5, confirmed: with no ?account=, no matching
		phone_number_id and no default incoming account, the webhook took
		the empty-payload branch and answered 200 OK -- the provider treats
		that as delivered and never retries, so the message is lost with a
		happy ack. It is now refused 400, naming what to configure."""
		payload = {
			"entry": [
				{
					"changes": [
						{
							"field": "messages",
							"value": {
								"metadata": {"phone_number_id": "no-such-phone-id"},
								"messages": [
									{
										"from": PHONE,
										"id": f"wamid.{frappe.generate_hash(length=10)}",
										"type": "text",
										"text": {"body": "nobody will read this"},
									}
								],
							},
						}
					]
				}
			]
		}
		body = json.dumps(payload).encode()
		request = _JsonRequest(body)  # no ?account=, no signature header

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

		self.assertEqual(
			response.status_code,
			400,
			"a webhook that resolves no account was answered 200 -- the provider "
			"stops retrying and the message is silently lost",
		)
		self.assertIn("No Relay Account resolved", response.get_data(as_text=True))
		row = frappe.db.get_value(
			"Relay Webhook Log",
			{"payload": ["like", "%nobody will read this%"]},
			["name", "status", "error_log"],
			as_dict=True,
			order_by="creation desc",
		)
		self.assertTrue(row, "the refusal was not recorded")
		self.assertEqual(row.status, "Failed")
		self.assertIn("No Relay Account resolved", row.error_log or "")
