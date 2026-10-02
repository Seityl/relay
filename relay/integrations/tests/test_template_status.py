# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""A template's status follows what the provider says (Seityl/relay#6).

Meta tells relay by webhook; Twilio has no such webhook, so relay asks it.
Either way the provider's word is translated into relay's vocabulary before
it is stored: `Relay Template.status` is a Select, and rxflow sends only
templates whose status is exactly "Approved".
"""

import json
import pathlib
import unittest
from unittest.mock import patch

import frappe
import requests
from frappe.tests import IntegrationTestCase

from relay.integrations import meta_cloud_api, twilio_adapter
from relay.integrations.registry import get_adapter
from relay.integrations.template_status import refresh_template_statuses
from relay.webhooks.handler import _process_normalized_payload

APP_ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE_JSON = APP_ROOT / "relay" / "doctype" / "relay_template" / "relay_template.json"

#: https://www.twilio.com/docs/content/content-types-overview#whatsapp-approval-statuses
#: (the API spells them in lower case: content-api-resources, example responses).
TWILIO_DOCUMENTED = {"unsubmitted", "received", "pending", "approved", "rejected", "paused", "disabled"}

#: https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/message_template_status_update
META_DOCUMENTED = {
	"APPROVED", "ARCHIVED", "DELETED", "DISABLED", "FLAGGED", "IN_APPEAL", "LIMIT_EXCEEDED",
	"LOCKED", "PAUSED", "PENDING", "PENDING_DELETION", "REINSTATED", "REJECTED", "UNARCHIVED",
}  # fmt: skip

TWILIO_CHANNEL = "Template Status Twilio Channel"
LONELY_CHANNEL = "Template Status Twilio Channel Without Account"
META_CHANNEL = "Template Status Meta Channel"
SID_A = "HX" + "a" * 32
SID_B = "HX" + "b" * 32


def _template_statuses() -> set:
	"""The Select options, read from the DocType rather than from memory."""
	for field in json.loads(TEMPLATE_JSON.read_text())["fields"]:
		if field["fieldname"] == "status":
			return set(field["options"].split("\n"))
	raise AssertionError("Relay Template has no status field any more")


class _Response:
	def __init__(self, status_code=200, payload=None):
		self.status_code = status_code
		self._payload = payload or {}
		self.text = json.dumps(self._payload)

	def json(self):
		return self._payload

	def raise_for_status(self):
		if self.status_code >= 400:
			raise requests.HTTPError(f"{self.status_code} Client Error", response=self)


def _approval(sid, status, reason=""):
	"""Twilio's documented ApprovalRequests response, with one status."""
	return {
		"sid": sid,
		"account_sid": "AC" + "0" * 32,
		"whatsapp": {
			"type": "whatsapp",
			"name": "refill_reminder",
			"category": "UTILITY",
			"content_type": "twilio/text",
			"status": status,
			"rejection_reason": reason,
			"allow_category_change": True,
		},
		"url": f"https://content.twilio.com/v1/Content/{sid}/ApprovalRequests",
	}


class _Account:
	name = "Template Status Unit Account"
	identifier_value = "15550002222"
	credentials = json.dumps({"account_sid": "AC" + "0" * 32})

	def get_access_token(self):
		return "test-auth-token"


class _Template:
	def __init__(self, name="refill_reminder-en", provider_template_id=SID_A):
		self.name = name
		self.provider_template_id = provider_template_id


class TestTemplateStatusVocabulary(unittest.TestCase):
	def test_every_twilio_approval_status_maps_to_one_relay_template_can_store(self):
		self.assertEqual(set(twilio_adapter.TEMPLATE_STATUS_MAP), TWILIO_DOCUMENTED)
		produced = set(twilio_adapter.TEMPLATE_STATUS_MAP.values())
		self.assertTrue(
			produced <= _template_statuses(),
			f"these map to statuses Relay Template cannot store: {sorted(produced - _template_statuses())}",
		)

	def test_every_meta_template_event_maps_to_one_relay_template_can_store_or_to_no_change(self):
		self.assertEqual(set(meta_cloud_api.TEMPLATE_STATUS_MAP), META_DOCUMENTED)
		produced = {v for v in meta_cloud_api.TEMPLATE_STATUS_MAP.values() if v is not None}
		self.assertTrue(
			produced <= _template_statuses(),
			f"these map to statuses Relay Template cannot store: {sorted(produced - _template_statuses())}",
		)

	def test_only_an_approval_makes_a_template_approved(self):
		"""rxflow sends a template only when it reads exactly "Approved"."""
		self.assertEqual({k for k, v in twilio_adapter.TEMPLATE_STATUS_MAP.items() if v == "Approved"}, {"approved"})
		self.assertEqual(
			{k for k, v in meta_cloud_api.TEMPLATE_STATUS_MAP.items() if v == "Approved"}, {"APPROVED", "REINSTATED"}
		)

	def test_a_disabled_template_is_not_called_rejected_or_paused(self):
		self.assertIn("Disabled", _template_statuses())
		self.assertEqual(twilio_adapter.TEMPLATE_STATUS_MAP.get("disabled"), "Disabled")
		self.assertEqual(meta_cloud_api.TEMPLATE_STATUS_MAP.get("DISABLED"), "Disabled")


class TestTwilioAsksForTemplateStatus(unittest.TestCase):
	def setUp(self):
		self.adapter = twilio_adapter.TwilioAdapter(_Account())

	def test_twilio_reads_a_templates_status_from_its_approval_request(self):
		with patch.object(twilio_adapter.requests, "get", return_value=_Response(200, _approval(SID_A, "approved"))) as get:
			event = self.adapter.fetch_template_status(_Template())

		self.assertEqual(get.call_args.args[0], f"https://content.twilio.com/v1/Content/{SID_A}/ApprovalRequests")
		self.assertEqual(get.call_args.kwargs["auth"], ("AC" + "0" * 32, "test-auth-token"))
		self.assertEqual(event.provider_template_id, SID_A)
		self.assertEqual(event.status, "Approved")
		self.assertEqual(event.provider_status, "approved")

	def test_twilio_is_understood_whichever_case_it_spells_a_status_in(self):
		# The API answers in lower case; Twilio's own docs table capitalises.
		with patch.object(twilio_adapter.requests, "get", return_value=_Response(200, _approval(SID_A, "Approved"))):
			event = self.adapter.fetch_template_status(_Template())

		self.assertEqual(event.status, "Approved")

	def test_a_twilio_status_relay_does_not_know_is_refused_naming_the_template(self):
		with patch.object(twilio_adapter.requests, "get", return_value=_Response(200, _approval(SID_A, "under_review"))):
			with self.assertRaises(frappe.ValidationError) as caught:
				self.adapter.fetch_template_status(_Template())

		self.assertIn("refill_reminder-en", str(caught.exception))
		self.assertIn("under_review", str(caught.exception))

	def test_a_template_without_a_content_sid_is_refused_naming_it(self):
		with patch.object(twilio_adapter.requests, "get") as get:
			with self.assertRaises(frappe.ValidationError) as caught:
				self.adapter.fetch_template_status(_Template(provider_template_id=""))

		get.assert_not_called()
		self.assertIn("refill_reminder-en", str(caught.exception))


class TestTemplateStatusFollowsTheProvider(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		for channel, provider in (
			(TWILIO_CHANNEL, "Twilio"),
			(LONELY_CHANNEL, "Twilio"),
			(META_CHANNEL, "Meta Cloud API"),
		):
			if not frappe.db.exists("Relay Channel", channel):
				frappe.get_doc(
					{"doctype": "Relay Channel", "channel_name": channel, "provider": provider, "enabled": 1}
				).insert(ignore_permissions=True)

		twilio = frappe.get_doc(
			{
				"doctype": "Relay Account",
				"account_name": "Template Status Twilio Account",
				"status": "Active",
				"channel": TWILIO_CHANNEL,
				"credentials": json.dumps({"account_sid": "AC" + "0" * 32}),
			}
		)
		twilio.access_token = "test-auth-token"
		cls.twilio_account = twilio.insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "Relay Account",
				"account_name": "Template Status Inactive Account",
				"status": "Inactive",
				"channel": LONELY_CHANNEL,
				"credentials": json.dumps({"account_sid": "AC" + "0" * 32}),
			}
		).insert(ignore_permissions=True)

		meta = frappe.get_doc(
			{
				"doctype": "Relay Account",
				"account_name": "Template Status Meta Account",
				"status": "Active",
				"channel": META_CHANNEL,
				"phone_number_id": "template-status-phone-id",
			}
		)
		meta.access_token = "test-meta-token"
		cls.meta_account = meta.insert(ignore_permissions=True)

	def setUp(self):
		# IntegrationTestCase rolls back per class, not per test.
		frappe.db.savepoint("template_status")
		self.addCleanup(frappe.db.rollback, save_point="template_status")

	def _template(self, channel, status, provider_template_id):
		return frappe.get_doc(
			{
				"doctype": "Relay Template",
				"template_name": f"tstatus_{frappe.generate_hash(length=8)}",
				"language_code": "en",
				"status": status,
				"channel": channel,
				"provider_template_id": provider_template_id,
				"body_text": "Your refill is due.",
			}
		).insert(ignore_permissions=True)

	def _status(self, template):
		return frappe.db.get_value("Relay Template", template.name, "status")

	def _poll(self, responses):
		"""Run the hourly job against canned Twilio answers, keyed by SID."""

		def get(url, **kwargs):
			for sid, response in responses.items():
				if f"/Content/{sid}/" in url:
					return response
			raise AssertionError(f"relay asked Twilio about something it should not have: {url}")

		with (
			patch.object(twilio_adapter.requests, "get", side_effect=get) as fake_get,
			patch("frappe.db.commit"),
			patch("frappe.log_error") as log_error,
		):
			refresh_template_statuses()
		return fake_get, log_error

	def _meta_webhook(self, provider_template_id, event):
		payload = {
			"object": "whatsapp_business_account",
			"entry": [
				{
					"id": "0",
					"changes": [
						{
							"field": "message_template_status_update",
							"value": {
								"event": event,
								"message_template_id": provider_template_id,
								"message_template_name": "refill_reminder",
								"message_template_language": "en",
								"reason": "NONE",
							},
						}
					],
				}
			],
		}
		adapter = get_adapter("Meta Cloud API", self.meta_account)
		with patch("frappe.log_error") as log_error:
			normalized = adapter.parse_inbound_webhook(payload, account_name=self.meta_account.name)
			_process_normalized_payload(normalized, self.meta_account.name, None)
		return log_error

	# --- Twilio: relay asks ------------------------------------------------

	def test_a_pending_twilio_template_becomes_approved_when_twilio_says_so(self):
		approved = self._template(TWILIO_CHANNEL, "Pending", SID_A)
		waiting = self._template(TWILIO_CHANNEL, "Pending", SID_B)
		for template in (approved, waiting):
			frappe.db.set_value("Relay Template", template.name, "modified", "2026-01-01 00:00:00", update_modified=False)

		self._poll({SID_A: _Response(200, _approval(SID_A, "approved")), SID_B: _Response(200, _approval(SID_B, "pending"))})

		self.assertEqual(self._status(approved), "Approved")
		self.assertEqual(self._status(waiting), "Pending")
		# Written through the document (Select validated; outside tests a
		# Version too -- Frappe skips Versions under test, document.py:577),
		# not by raw SQL, which leaves `modified` where it was.
		self.assertGreater(
			str(frappe.db.get_value("Relay Template", approved.name, "modified")),
			"2026-01-01 00:00:00",
			f"{approved.name} changed status without its document being saved",
		)
		# Nothing changed, so nothing is saved: every template would
		# otherwise look modified every hour.
		self.assertEqual(str(frappe.db.get_value("Relay Template", waiting.name, "modified")), "2026-01-01 00:00:00")

	def test_one_templates_failure_does_not_stop_the_next_and_names_it(self):
		missing = self._template(TWILIO_CHANNEL, "Pending", SID_A)
		approved = self._template(TWILIO_CHANNEL, "Pending", SID_B)

		_, log_error = self._poll(
			{SID_A: _Response(404, {"code": 20404, "message": "not found"}), SID_B: _Response(200, _approval(SID_B, "approved"))}
		)

		self.assertEqual(self._status(missing), "Pending")
		self.assertEqual(self._status(approved), "Approved")
		logged = " ".join(str(c) for c in log_error.call_args_list)
		self.assertIn(missing.name, logged)
		self.assertIn("HTTP 404", logged)

	def test_a_template_whose_channel_has_only_an_inactive_account_is_reported_not_polled(self):
		lonely = self._template(LONELY_CHANNEL, "Pending", SID_A)

		fake_get, log_error = self._poll({})

		fake_get.assert_not_called()
		self.assertEqual(self._status(lonely), "Pending")
		logged = " ".join(str(c) for c in log_error.call_args_list)
		self.assertIn(lonely.name, logged)
		self.assertIn(LONELY_CHANNEL, logged)

	def test_a_template_relay_holds_no_provider_id_for_is_not_asked_about(self):
		draft = self._template(TWILIO_CHANNEL, "Draft", "")

		fake_get, log_error = self._poll({})

		fake_get.assert_not_called()
		self.assertEqual(self._status(draft), "Draft")
		self.assertNotIn(draft.name, " ".join(str(c) for c in log_error.call_args_list))

	def test_a_template_on_a_channel_that_pushes_its_status_is_not_polled(self):
		self._template(META_CHANNEL, "Pending", "1689556908129832")

		from relay.integrations import template_status

		with patch.object(template_status, "get_adapter", wraps=template_status.get_adapter) as built:
			fake_get, _ = self._poll({})

		fake_get.assert_not_called()
		self.assertNotIn("Meta Cloud API", [c.args[0] for c in built.call_args_list])

	def test_the_poll_runs_every_hour(self):
		self.assertIn(
			"relay.integrations.template_status.refresh_template_statuses",
			frappe.get_hooks("scheduler_events").get("hourly", []),
		)

	# --- Meta: the provider tells ------------------------------------------

	def test_a_meta_approval_is_stored_as_relay_spells_it(self):
		template = self._template(META_CHANNEL, "Pending", "1689556908129832")

		# Meta sends the id as a number.
		self._meta_webhook(1689556908129832, "APPROVED")

		self.assertEqual(self._status(template), "Approved")

	def test_a_meta_template_id_reaches_relay_as_text(self):
		# Meta sends a number; Relay Template.provider_template_id is Data and
		# TemplateStatusEvent says str. (MariaDB 10.11 happens to compare the
		# number with the text exactly; the contract should not rest on that.)
		adapter = get_adapter("Meta Cloud API", self.meta_account)
		payload = {
			"entry": [
				{
					"changes": [
						{
							"field": "message_template_status_update",
							"value": {"event": "APPROVED", "message_template_id": 1689556908129832},
						}
					]
				}
			]
		}

		event = adapter.parse_inbound_webhook(payload).template_status_events[0]

		self.assertEqual(event.provider_template_id, "1689556908129832")

	def test_a_flagged_meta_template_stays_approved(self):
		template = self._template(META_CHANNEL, "Approved", "1689556908129833")

		self._meta_webhook(1689556908129833, "FLAGGED")

		self.assertEqual(self._status(template), "Approved")

	def test_a_meta_event_relay_does_not_know_leaves_the_template_unchanged_and_says_so(self):
		template = self._template(META_CHANNEL, "Approved", "1689556908129834")

		log_error = self._meta_webhook(1689556908129834, "SOMETHING_NEW")

		self.assertEqual(self._status(template), "Approved")
		logged = " ".join(str(c) for c in log_error.call_args_list)
		self.assertIn("SOMETHING_NEW", logged)
		self.assertIn("1689556908129834", logged)
