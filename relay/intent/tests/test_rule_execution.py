# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""A matched rule is actually applied to the thread (#9).

`classify_message` raised on every single inbound message, and the caller
swallows it:

    # webhooks/handler.py
    try:
        classify_message(msg_doc, thread, contact)
    except Exception:
        frappe.log_error(title="Relay Intent Classification Failed")

so the only trace was a log nobody read. Captured from a real inbound
WhatsApp message:

    frappe.exceptions.TimestampMismatchError: <thread> (Relay Thread) has
    been modified after you have opened it
    (…04:38:09.710422, …04:38:09.720987)

Ten milliseconds apart, and both writes are relay's own. The handler loads
the thread, inserts the message, and `RelayMessage.after_insert` immediately
re-saves that same thread through a *different* object:

    thread = frappe.get_doc("Relay Thread", self.thread)
    thread.update_timestamps(self.creation, self.direction)

By the time the handler passes its copy to `classify_message`, the copy is
stale -- not sometimes, but on every inbound message, because the insert
that triggers classification is the same insert that invalidates the thread.

What was lost each time, in `_execute_rule` order: the thread status the rule
sets, the assignment, the tags, the automated reply, and -- because
`process_stop_request` runs *after* the save that raised -- the opt-out. A
customer replying STOP was never unsubscribed, silently.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from relay.intent.engine import classify_message

ACCOUNT = "Rule Execution Test Account"


class TestRuleExecution(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		cls.contact = frappe.get_doc(
			{
				"doctype": "Relay Contact",
				"display_name": "Rule Execution Test",
				"identifiers": [
					{
						"identifier_type": "Phone",
						"identifier_value": "15550009999",
						"is_primary": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		cls.account = frappe.get_doc(
			{
				"doctype": "Relay Account",
				"account_name": ACCOUNT,
				"status": "Active",
				# An existing channel, so this is app configuration not test data.
				"channel": "Email",
			}
		).insert(ignore_permissions=True)

	def _classify(self, message, held):
		"""Classify without letting the engine commit the test transaction.

		`process_stop_request` commits, and a commit inside a test commits
		the test's own open transaction -- `IntegrationTestCase` then has
		nothing left to roll back. The first green run of this file left a
		Contact, an Account, two Threads, two Messages and a Consent Log on
		the site, which had to be removed by document ID.
		"""
		with (
			patch(
				"relay.intent.engine.send_auto_reply",
				return_value={"success": True, "message_id": "stub"},
			),
			patch("frappe.db.commit"),
		):
			return classify_message(message, held, self.contact)

	def _inbound(self, body):
		"""Reproduce the handler's ordering exactly.

		The order is the bug: the thread is loaded, *then* the message is
		inserted, and the insert re-saves the thread underneath the caller.
		Loading the thread afterwards would hide the defect entirely.
		"""
		thread = frappe.get_doc(
			{
				"doctype": "Relay Thread",
				"contact": self.contact.name,
				"account": self.account.name,
				"status": "Open",
			}
		).insert(ignore_permissions=True)

		# The caller's handle, taken before the insert that invalidates it.
		held_by_caller = frappe.get_doc("Relay Thread", thread.name)

		message = frappe.get_doc(
			{
				"doctype": "Relay Message",
				"thread": thread.name,
				"contact": self.contact.name,
				"account": self.account.name,
				"direction": "Incoming",
				"status": "Delivered",
				"content_type": "text",
				"message_body": body,
				"message_id": frappe.generate_hash(length=12),
			}
		).insert(ignore_permissions=True)

		return message, held_by_caller

	# --- the premise -----------------------------------------------------

	def test_the_thread_the_caller_holds_is_stale_once_the_message_is_inserted(self):
		"""Why a reload is needed, asked of the database rather than assumed.

		If this ever fails, `after_insert` stopped re-saving the thread and
		the reload in `_execute_rule` is no longer load-bearing -- at which
		point it should be removed deliberately, not left as cargo.
		"""
		_, held = self._inbound("hello")

		on_disk = frappe.db.get_value("Relay Thread", held.name, "modified")

		self.assertNotEqual(
			str(held.modified),
			str(on_disk),
			"the caller's thread copy is not stale, so this file is testing "
			"a collision that no longer happens",
		)

	# --- #9: the rule is applied -----------------------------------------

	def test_a_matched_rule_moves_the_thread_it_was_handed(self):
		"""The defect. Before the fix this raised TimestampMismatchError."""
		message, held = self._inbound("hello")

		result = self._classify(message, held)

		self.assertTrue(result.matched, "no rule matched a greeting at all")

		status = frappe.db.get_value("Relay Thread", held.name, "status")
		self.assertNotEqual(
			status,
			"Open",
			"the thread was left Open, so the matched rule's set_thread_status "
			"never reached the database and routing silently does nothing",
		)

	def test_a_stop_message_unsubscribes_the_contact(self):
		"""The consequence that matters most.

		`process_stop_request` runs after the thread save inside
		`_execute_rule`, so the save raising meant an opt-out was never
		honoured -- and the failure was swallowed into a log.
		"""
		message, held = self._inbound("STOP")

		self._classify(message, held)

		self.assertTrue(
			frappe.db.get_value("Relay Contact", self.contact.name, "is_blocked"),
			"a contact who replied STOP was not blocked; they will keep "
			"receiving messages",
		)
		self.assertTrue(
			frappe.db.exists(
				"Relay Consent Log",
				{"contact": self.contact.name, "event_type": "Opt-Out"},
			),
			"no Opt-Out was recorded, so there is no audit trail for the "
			"unsubscribe",
		)

	def test_classification_does_not_raise_on_an_ordinary_inbound_message(self):
		"""The blunt one.

		The handler swallows whatever `classify_message` raises, so nothing
		downstream notices. This asserts it does not raise in the first
		place.
		"""
		message, held = self._inbound("I need a refill please")

		try:
			self._classify(message, held)
		except Exception as e:
			self.fail(f"classification raised {type(e).__name__}: {e}")
