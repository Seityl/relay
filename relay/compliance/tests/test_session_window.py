# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""The 24-hour session window on WhatsApp channels (Seityl/relay#7).

WhatsApp allows freeform business messages only within 24 hours of the
customer's last inbound message; outside it, only an approved template may
go. Relay decides freeform-vs-template from the caller's arguments alone, so
it queues messages the provider is certain to refuse. These tests pin the
refusal at the queue's front door: before a Relay Message row and a queue row
exist, with a message that names the thread and the age of the last inbound.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from relay.integrations.base_adapter import PermanentRejection

CONTACT_PHONE = "15557001111"

OUTSIDE_WINDOW = {"days": -3}
INSIDE_WINDOW = {"hours": -1}


class TestSessionWindow(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		cls.contact = frappe.get_doc(
			{
				"doctype": "Relay Contact",
				"display_name": "Session Window Test",
				"identifiers": [
					{
						"identifier_type": "Phone",
						"identifier_value": CONTACT_PHONE,
						"is_primary": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		cls.twilio_account = cls._account("Session Window Twilio", "Twilio")
		cls.meta_account = cls._account("Session Window Meta", "Meta Cloud API")
		cls.email_account = cls._account("Session Window Email", "Email")

	@classmethod
	def _account(cls, name, channel):
		return frappe.get_doc(
			{
				"doctype": "Relay Account",
				"account_name": name,
				"status": "Active",
				"channel": channel,
			}
		).insert(ignore_permissions=True)

	def setUp(self):
		# IntegrationTestCase rolls back per class, not per test.
		frappe.db.savepoint("session_window")
		self.addCleanup(frappe.db.rollback, save_point="session_window")

	def _thread(self, account, last_inbound_at=None):
		"""A thread for the class contact, optionally with a session stamp."""
		thread = frappe.get_doc(
			{
				"doctype": "Relay Thread",
				"contact": self.contact.name,
				"account": account.name,
				"status": "Open",
			}
		).insert(ignore_permissions=True)
		if last_inbound_at:
			frappe.db.set_value(
				"Relay Thread", thread.name, "last_inbound_at", last_inbound_at
			)
		return thread

	def _inbound(self, account, thread, created_at=None):
		"""An incoming message on the thread, its creation set to `created_at`.

		Simulates "the customer last wrote at `created_at`" the way the
		backfill leaves history: the message row and the thread's stamp
		agree.
		"""
		message = frappe.get_doc(
			{
				"doctype": "Relay Message",
				"thread": thread.name,
				"contact": self.contact.name,
				"account": account.name,
				"direction": "Incoming",
				"status": "Delivered",
				"content_type": "text",
				"message_body": "from the customer",
			}
		).insert(ignore_permissions=True)
		if created_at:
			frappe.db.set_value("Relay Message", message.name, "creation", created_at)
			frappe.db.set_value("Relay Thread", thread.name, "last_inbound_at", created_at)
		return message

	def _send(self, account, template="", **kwargs):
		"""Call send_message the way callers do, with its commit suppressed.

		send_message commits; a commit here would end the class transaction
		the savepoint lives in (test_template_status.py:250 patches the same
		way).
		"""
		from relay.relay.doctype.relay_message.relay_message import send_message

		with patch("frappe.db.commit"):
			return send_message(
				recipient_type="Phone",
				recipient_value=CONTACT_PHONE,
				message_body="" if template else "hello",
				template=template,
				template_parameters={"1": "x"} if template else None,
				account=account.name,
				**kwargs,
			)

	def _outgoing_messages(self):
		return frappe.db.count(
			"Relay Message", {"contact": self.contact.name, "direction": "Outgoing"}
		)

	def test_a_freeform_send_inside_the_window_is_queued(self):
		thread = self._thread(self.twilio_account)
		message = self._inbound(self.twilio_account, thread)
		# after_insert stamps the thread through update_timestamps
		self.assertIsNotNone(
			frappe.db.get_value("Relay Thread", thread.name, "last_inbound_at")
		)
		self.assertIsNotNone(message)

		result = self._send(self.twilio_account)

		self.assertTrue(result["success"])
		self.assertEqual(
			frappe.db.count("Relay Outbound Queue", {"thread": thread.name}), 1
		)

	def test_a_freeform_send_outside_the_window_names_the_thread_and_its_age(self):
		thread = self._thread(self.twilio_account)
		self._inbound(
			self.twilio_account, thread, created_at=add_to_date(now_datetime(), **OUTSIDE_WINDOW)
		)

		with self.assertRaises(PermanentRejection) as refused:
			self._send(self.twilio_account)

		text = str(refused.exception)
		self.assertIn(thread.name, text)
		self.assertIn("3 days ago", text)

		# Refused before queueing: no message row, no queue row.
		self.assertEqual(self._outgoing_messages(), 0)
		self.assertEqual(
			frappe.db.count("Relay Outbound Queue", {"contact": self.contact.name}), 0
		)

	def test_a_freeform_send_on_a_contact_who_never_messaged_names_that(self):
		with self.assertRaises(PermanentRejection) as refused:
			self._send(self.twilio_account)

		text = str(refused.exception)
		self.assertIn(self.contact.name, text)
		self.assertIn("never", text)
		self.assertEqual(self._outgoing_messages(), 0)

	def test_a_template_send_outside_the_window_is_still_queued(self):
		"""The window's whole point: outside it, an approved template may go."""
		template = frappe.get_doc(
			{
				"doctype": "Relay Template",
				"template_name": "session-window-test-template",
				"status": "Approved",
				"body_text": "Hello {{1}}",
			}
		).insert(ignore_permissions=True)
		self._thread(
			self.twilio_account, last_inbound_at=add_to_date(now_datetime(), **OUTSIDE_WINDOW)
		)

		result = self._send(self.twilio_account, template=template.name)

		self.assertTrue(result["success"])
		self.assertEqual(self._outgoing_messages(), 1)

	def test_an_interactive_send_is_not_window_checked(self):
		"""#7 scopes the pre-queue check to freeform messages.

		An interactive send outside the window is refused by the provider
		(63016 / 131047) and lands Refused through the adapter signal, not
		here. This test pins that boundary so widening it is a decision.
		"""
		self._thread(
			self.twilio_account, last_inbound_at=add_to_date(now_datetime(), **OUTSIDE_WINDOW)
		)

		result = self._send(
			self.twilio_account, interactive_payload={"type": "button", "body": "pick one"}
		)

		self.assertTrue(result["success"])

	def test_the_window_does_not_bind_email(self):
		"""The window is a WhatsApp rule; an Email channel has none."""
		self._thread(
			self.email_account, last_inbound_at=add_to_date(now_datetime(), **OUTSIDE_WINDOW)
		)

		result = self._send(self.email_account)

		self.assertTrue(result["success"])

	def test_the_window_binds_the_meta_channel_too(self):
		"""Both WhatsApp providers enforce it, each by its own key."""
		self._thread(
			self.meta_account, last_inbound_at=add_to_date(now_datetime(), **OUTSIDE_WINDOW)
		)

		with self.assertRaises(PermanentRejection):
			self._send(self.meta_account)

	def test_an_outbound_message_does_not_open_the_session(self):
		"""The anchor is the contact's last INBOUND message.

		A template relay sent an hour ago must not read as a session: the
		window is about when the customer last wrote.
		"""
		thread = self._thread(self.twilio_account)
		self._inbound(
			self.twilio_account, thread, created_at=add_to_date(now_datetime(), days=-4)
		)
		frappe.get_doc(
			{
				"doctype": "Relay Message",
				"thread": thread.name,
				"contact": self.contact.name,
				"account": self.twilio_account.name,
				"direction": "Outgoing",
				"status": "Sent",
				"content_type": "text",
				"message_body": "a template relay sent earlier",
			}
		).insert(ignore_permissions=True)

		with self.assertRaises(PermanentRejection):
			self._send(self.twilio_account)

	def test_the_window_follows_the_provider_not_the_channel_name(self):
		"""A channel may be named anything; the window keys on its provider."""
		channel = frappe.get_doc(
			{
				"doctype": "Relay Channel",
				"channel_name": "Session Window Oddly Named Channel",
				"provider": "Twilio",
				"enabled": 1,
				"adapter_class": "relay.integrations.twilio_adapter.TwilioAdapter",
				"handler_module": "relay.integrations.twilio_adapter",
				"webhook_path": "/api/method/relay.webhooks.handler.receive",
			}
		).insert(ignore_permissions=True)
		account = self._account("Session Window Oddly Named", channel.name)
		self._thread(
			account, last_inbound_at=add_to_date(now_datetime(), **OUTSIDE_WINDOW)
		)

		with self.assertRaises(PermanentRejection):
			self._send(account)

	def test_the_session_follows_the_contact_across_threads(self):
		"""The window is the provider's, keyed on the contact -- not on one
		relay thread.

		With the default auto-reply rules an inbound can move its thread out
		of Open and the reply opens a new one (recorded on Seityl/rxflow#111
		from the #15 verify). A check anchored on the reply's fresh thread
		would refuse a reply to a message that arrived seconds ago. The
		latest inbound on ANY of the contact's threads on the account opens
		the session.
		"""
		old_thread = self._thread(self.twilio_account)
		message = frappe.get_doc(
			{
				"doctype": "Relay Message",
				"thread": old_thread.name,
				"contact": self.contact.name,
				"account": self.twilio_account.name,
				"direction": "Incoming",
				"status": "Delivered",
				"content_type": "text",
				"message_body": "from the customer",
			}
		).insert(ignore_permissions=True)
		# The defect: the inbound's thread is no longer Open, so the reply
		# rides a brand-new thread that has never heard from the contact.
		frappe.db.set_value("Relay Thread", old_thread.name, "status", "Resolved")

		result = self._send(self.twilio_account)

		self.assertTrue(result["success"])
		# The new thread carries the queued reply...
		new_threads = frappe.get_all(
			"Relay Thread",
			filters={"contact": self.contact.name, "status": "Open"},
			pluck="name",
		)
		self.assertEqual(len(new_threads), 1)
		self.assertEqual(
			frappe.db.count("Relay Outbound Queue", {"thread": new_threads[0]}), 1
		)
		# ...and the refusal decision was made on the contact's session,
		# not on the new thread's empty history.
		self.assertIsNotNone(message)
