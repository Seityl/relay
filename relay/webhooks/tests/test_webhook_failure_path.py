# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""The webhook's controlled-500 path records its failure (#30).

`_handle_post`'s except clause intends to record the failure and answer a
controlled 500. But the Webhook Log row was INSERTED inside the same
transaction, and the except calls `frappe.db.rollback()` before saving it —
so the save hit a row the rollback had just discarded
(DoesNotExistError), every exception reaching that except escaped as a
secondary error, and no Failed record ever landed anywhere.

These tests drive `_handle_post` the way Meta does -- a signed JSON POST --
with the persist patched at its own seam, which is where "parseable" ends
and "persistable" begins. They are the first tests to drive the failure
path's recording itself.

Fixtures are per-test, not per-class, for the same reason
test_persist_inbound_message.py's are: a failing drive reaches the
handler's `frappe.db.rollback()`, and a rollback inside the class
transaction wipes every fixture the class had already made.
"""

import hashlib
import hmac
import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from relay.webhooks import handler
from relay.webhooks.handler import _handle_post

APP_SECRET = "failure-path-test-app-secret"
ACCOUNT = "Meta-Failure-Path-Test"
CHANNEL = "Meta Failure Path Test Channel"
ORIGIN = "https://relay.test"
PATH = "/api/method/relay.webhooks.handler.receive"
QUERY = f"account={ACCOUNT}"
PHONE = "15550008888"
PHONE_ID = "failure-path-test-phone-id"


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

	def __init__(self, body: bytes):
		self.data = body
		# One innocuous header so the Failed row's headers carry-over is
		# observable (an empty dict would make the assert vacuous).
		self.headers = _Headers({"user-agent": "failure-path-test/1.0"})
		self.url = f"{ORIGIN}{PATH}?{QUERY}"
		self.path = PATH
		self.query_string = QUERY.encode()

	@property
	def form(self):
		return _Form({})

	@property
	def args(self):
		return _Form({"account": ACCOUNT})


class TestTheWebhookFailurePath(IntegrationTestCase):
	def setUp(self):
		"""Fresh channel + account per test, not per class.

		Today's failing drive reaches _handle_post's except, which calls
		frappe.db.rollback() -- inside the test transaction that holds the
		class fixtures. A per-class fixture would be wiped for every test
		after the first failing one (the m5c class-transaction lesson,
		already paid in #28's first red run).
		"""
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

		if not frappe.db.exists("Relay Account", {"account_name": ACCOUNT}):
			frappe.get_doc(
				{
					"doctype": "Relay Account",
					"account_name": ACCOUNT,
					"status": "Active",
					"channel": CHANNEL,
					"app_secret": APP_SECRET,
					"access_token": "failure-path-test-access-token",
					"phone_number_id": PHONE_ID,
					"api_url": "https://graph.facebook.com",
					"api_version": "v21.0",
				}
			).insert(ignore_permissions=True)

	def _post_meta(self, payload: dict, persist_side_effect):
		"""Drive the webhook the way Meta does: a signed JSON POST. The
		persist is patched at its own seam (the handler's
		_persist_inbound_message), which is the point past parsing where a
		real failure occurs. `frappe.db.commit` is suppressed (the handler
		commits several places; a real commit would defeat the class
		rollback -- see test_signature_gate.py's _post). `frappe.log_error`
		is captured rather than allowed to run: it commits, which would
		poison the suite's open transaction (the 2026-09-28 side-row
		lesson); the tests assert on the captured calls."""
		body = json.dumps(payload).encode()
		signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
		request = _JsonRequest(body)
		with (
			patch("frappe.request", request),
			patch("frappe.db.commit"),
			patch("frappe.utils.get_url", return_value=ORIGIN),
			patch("frappe.log_error") as log_error,
			patch(
				"relay.webhooks.handler._persist_inbound_message",
				side_effect=persist_side_effect,
			),
		):
			frappe.local.form_dict = frappe._dict()
			try:
				response = _handle_post()
			finally:
				frappe.local.form_dict = frappe._dict()
		self.log_error = log_error
		return response

	def _text_payload(self, media_ids: list) -> dict:
		"""A Meta text-message batch. Text parses without any media
		download (meta_cloud_api.py parses message["text"]["body"]), so the
		only failure point is the persist seam these tests patch."""
		return {
			"entry": [
				{
					"changes": [
						{
							"field": "messages",
							"value": {
								"metadata": {"phone_number_id": PHONE_ID},
								"contacts": [
									{"wa_id": PHONE, "profile": {"name": "failure path test patient"}}
								],
								"messages": [
									{
										"from": PHONE,
										"id": f"wamid.{media_id}",
										"type": "text",
										"text": {"body": f"failure path probe {media_id}"},
									}
									for media_id in media_ids
								],
							},
						}
					]
				}
			]
		}

	def _failed_log_row(self, media_id: str):
		return frappe.db.get_value(
			"Relay Webhook Log",
			{"payload": ["like", f"%{media_id}%"]},
			["name", "status", "error_log", "payload", "headers"],
			as_dict=True,
			order_by="creation desc",
		)

	def test_a_failing_persist_answers_500_and_records_a_failed_webhook_log(self):
		"""#30: the drive raises at the persist seam. The handler must answer
		a CONTROLLED 500 and record a Failed Webhook Log row carrying the
		traceback and the original row's payload/headers -- instead of
		exploding on the stale object it just rolled back, which is what
		turned every failure into a secondary exception with no record."""
		media_id = frappe.generate_hash(length=12)
		response = self._post_meta(
			self._text_payload([media_id]),
			OSError("Truncated File Read"),
		)

		self.assertEqual(response.status_code, 500, "a failing persist did not answer a controlled 500")
		row = self._failed_log_row(media_id)
		self.assertTrue(row, "no Relay Webhook Log row recorded the failure")
		self.assertEqual(row.status, "Failed", "the recorded row is not Failed")
		self.assertIn("Truncated File Read", row.error_log or "", "the traceback was not recorded")
		self.assertIn(media_id, row.payload or "", "the Failed row does not carry the drive's payload")
		self.assertIn("user-agent", (row.headers or "").lower(), "the Failed row does not carry the headers")
		count = frappe.db.count("Relay Webhook Log", {"payload": ["like", f"%{media_id}%"]})
		self.assertEqual(count, 1, "the failure was recorded more than once")
		# The call THIS guard makes. Nothing else logs against a Webhook Log
		# row, but the M3 lesson stands: the assertion pins the title prefix
		# AND the reference, so it cannot pass for the wrong reason.
		named = [
			c
			for c in self.log_error.call_args_list
			if str(c.kwargs.get("title", "")).startswith("Relay webhook processing failed")
			and c.kwargs.get("reference_name") == row.name
		]
		self.assertTrue(named, f"the failure was not logged naming the row: {self.log_error.call_args_list}")

	def test_a_failed_webhook_leaves_no_partial_rows_behind(self):
		"""#30, the rollback half: the first message of the batch persists
		for real, the second one's persist raises. The 500 must mean the
		partial persist is GONE (the provider's retry starts clean instead
		of duplicating against _is_duplicate) -- and the Failed record must
		survive the rollback (a rollback that costs the record is the #30
		defect itself)."""
		first, second = frappe.generate_hash(length=12), frappe.generate_hash(length=12)
		real_persist = handler._persist_inbound_message
		persisted = []

		def first_persists_then_die(message, account_name):
			if not persisted:
				persisted.append(message.provider_message_id)
				return real_persist(message, account_name)
			raise OSError("Truncated File Read")

		response = self._post_meta(
			self._text_payload([first, second]),
			first_persists_then_die,
		)

		self.assertEqual(response.status_code, 500, "a failing persist did not answer a controlled 500")
		row = self._failed_log_row(first)
		self.assertTrue(row, "no Relay Webhook Log row recorded the failure")
		self.assertEqual(row.status, "Failed", "the recorded row is not Failed")
		self.assertFalse(
			frappe.db.exists("Relay Message", {"message_id": f"wamid.{first}", "direction": "Incoming"}),
			"the first message's rows survived the 500 -- the partial persist was not rolled back",
		)
		self.assertFalse(
			frappe.db.exists("Relay Message", {"message_id": f"wamid.{second}", "direction": "Incoming"}),
			"the second message's rows survived the 500",
		)
