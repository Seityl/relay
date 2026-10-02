# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""A matched rule is actually applied to the thread (#9), and the write
survives overlapping inbounds (#22).

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
writes that same thread's row through a *different* object:

    thread = frappe.get_doc("Relay Thread", self.thread)
    thread.update_timestamps(self.creation, self.direction)

By the time the handler passes its copy to `classify_message`, the copy is
stale -- not sometimes, but on every inbound message, because the insert
that triggers classification is the same insert that invalidates the thread.

What was lost each time, in `_execute_rule` order: the thread status the rule
sets, the assignment, the tags, the automated reply, and -- because
`process_stop_request` runs *after* the save that raised -- the opt-out. A
customer replying STOP was never unsubscribed, silently.

#9 fixed the collision by reloading before the rule's save. #22 went further:
the inbound's thread write and the rule's thread write are now targeted
writes (an atomic UPDATE and a db.set_value) with no optimistic locking, so
an inbound landing while a rule applies neither raises nor regresses the
counters. The copy the caller holds is still stale in field values -- that
premise is pinned by
test_the_thread_the_caller_holds_is_stale_once_the_message_is_inserted.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_datetime

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

	def setUp(self):
		# IntegrationTestCase rolls back per class, not per test -- and this
		# class's tests share the contact: test_a_stop_message_unsubscribes
		# blocks it, and without a savepoint every later test inherits the
		# block and fails for a reason that is not the one under test.
		frappe.db.savepoint("rule_execution")
		self.addCleanup(frappe.db.rollback, save_point="rule_execution")
		# The savepoint restores the ROW, not the class fixture object:
		# process_stop_request set is_blocked on the shared object in memory.
		self.contact.reload()

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

	# --- #22: the engine's write must not lose a concurrent inbound -------

	def test_an_inbound_landing_while_a_rule_applies_is_not_lost_and_does_not_collide(self):
		"""#22: a second inbound landing between the engine's read of the
		thread and its write of the rule's actions must survive.

		The engine used to save the whole thread document it had reloaded;
		an inbound landing in that window either collided with the save
		(TimestampMismatchError, swallowed by the handler, rule lost) or
		wrote stale counters back. The overlap is simulated deterministically:
		while the rule's thread write is in flight, the second inbound's
		insert runs -- the way two concurrent requests genuinely interleave.
		"""
		thread = frappe.get_doc(
			{
				"doctype": "Relay Thread",
				"contact": self.contact.name,
				"account": self.account.name,
				"status": "Open",
			}
		).insert(ignore_permissions=True)
		held = frappe.get_doc("Relay Thread", thread.name)

		message = frappe.get_doc(
			{
				"doctype": "Relay Message",
				"thread": thread.name,
				"contact": self.contact.name,
				"account": self.account.name,
				"direction": "Incoming",
				"status": "Delivered",
				"content_type": "text",
				"message_body": "hello",
				"message_id": frappe.generate_hash(length=12),
			}
		).insert(ignore_permissions=True)

		real_set_value = frappe.db.set_value
		landed = []

		def inbound_lands_mid_apply(doctype, name=None, *args, **kwargs):
			if doctype == "Relay Thread" and name == thread.name and not landed:
				landed.append(
					frappe.get_doc(
						{
							"doctype": "Relay Message",
							"thread": thread.name,
							"contact": self.contact.name,
							"account": self.account.name,
							"direction": "Incoming",
							"status": "Delivered",
							"content_type": "text",
							"message_body": "I need a refill please",
							"message_id": frappe.generate_hash(length=12),
						}
					).insert(ignore_permissions=True)
				)
			return real_set_value(doctype, name, *args, **kwargs)

		with (
			patch(
				"relay.intent.engine.send_auto_reply",
				return_value={"success": True, "message_id": "stub"},
			),
			patch("frappe.db.commit"),
			patch("frappe.db.set_value", side_effect=inbound_lands_mid_apply),
		):
			result = classify_message(message, held, self.contact)

		self.assertTrue(result.matched, "no rule matched a greeting at all")
		self.assertTrue(landed, "the concurrent inbound never landed, so this test exercised no overlap")
		self.assertEqual(
			frappe.db.get_value("Relay Thread", thread.name, "status"),
			"Bot Handling",
			"the matched rule's set_thread_status never reached the database",
		)
		self.assertEqual(
			frappe.db.get_value("Relay Thread", thread.name, "unread_count"),
			2,
			"the inbound that landed while the rule applied lost its count",
		)
		self.assertEqual(
			get_datetime(frappe.db.get_value("Relay Thread", thread.name, "last_message_at")),
			get_datetime(landed[0].creation),
			"the rule's write regressed the stamp of the inbound that landed mid-apply",
		)

	# --- #22: the reply stays on the conversation's thread and account ----

	def test_a_stop_goodbye_lands_on_the_thread_it_closes(self):
		"""#22: the goodbye that ends a conversation belongs to that
		conversation.

		The Stop rule closes the thread and then sends its goodbye; the old
		lookup matched only "Open" threads, so the goodbye opened a fresh
		thread of its own -- and the next message from the contact landed
		there, not on the thread that received the STOP.
		"""
		message, held = self._inbound("STOP")
		# The real send path: the goodbye's message row is the point.
		with patch("frappe.db.commit"):
			result = classify_message(message, held, self.contact)

		self.assertTrue(result.matched, "no rule matched the STOP")
		goodbye = frappe.get_all(
			"Relay Message",
			filters={"contact": self.contact.name, "direction": "Outgoing"},
			fields=["name", "thread"],
			order_by="creation desc",
			limit=1,
		)
		self.assertEqual(len(goodbye), 1, "no goodbye was recorded at all")
		self.assertEqual(
			goodbye[0].thread,
			message.thread,
			"the goodbye opened a thread of its own instead of closing the conversation on it",
		)

	def test_the_opt_out_is_honoured_even_when_the_default_outgoing_account_has_no_session(self):
		"""#22: the opt-out must survive the goodbye's window check.

		send_auto_reply used to send on the DEFAULT OUTGOING account; on a
		multi-account site the goodbye's freeform window check then ran
		against an account the contact never wrote to, raised
		PermanentRejection, and -- swallowed by the handler -- the STOP
		never took effect. The conversation's own account has an open
		session by definition: the inbound that triggered the rule just
		landed on it.
		"""
		default_outgoing = frappe.get_doc(
			{
				"doctype": "Relay Account",
				"account_name": "Rule Execution Default Outgoing",
				"status": "Active",
				"channel": "Twilio",
				"is_default_outgoing": 1,
			}
		).insert(ignore_permissions=True)
		conversation_account = frappe.get_doc(
			{
				"doctype": "Relay Account",
				"account_name": "Rule Execution Conversation",
				"status": "Active",
				"channel": "Twilio",
			}
		).insert(ignore_permissions=True)
		thread = frappe.get_doc(
			{
				"doctype": "Relay Thread",
				"contact": self.contact.name,
				"account": conversation_account.name,
				"status": "Open",
			}
		).insert(ignore_permissions=True)
		held = frappe.get_doc("Relay Thread", thread.name)
		message = frappe.get_doc(
			{
				"doctype": "Relay Message",
				"thread": thread.name,
				"contact": self.contact.name,
				"account": conversation_account.name,
				"direction": "Incoming",
				"status": "Delivered",
				"content_type": "text",
				"message_body": "STOP",
				"message_id": frappe.generate_hash(length=12),
			}
		).insert(ignore_permissions=True)

		with patch("frappe.db.commit"):
			result = classify_message(message, held, self.contact)

		self.assertTrue(result.matched, "no rule matched the STOP")
		self.assertTrue(
			frappe.db.get_value("Relay Contact", self.contact.name, "is_blocked"),
			"a contact who replied STOP was not blocked; the goodbye's window "
			"check against the wrong account ate the opt-out",
		)
		self.assertTrue(
			frappe.db.exists(
				"Relay Consent Log",
				{"contact": self.contact.name, "event_type": "Opt-Out"},
			),
			"no Opt-Out was recorded, so there is no audit trail for the unsubscribe",
		)
		goodbye = frappe.get_all(
			"Relay Message",
			filters={
				"contact": self.contact.name,
				"direction": "Outgoing",
				"account": conversation_account.name,
			},
			fields=["name", "thread"],
			order_by="creation desc",
			limit=1,
		)
		self.assertEqual(len(goodbye), 1, "no goodbye went out on the conversation's account")
		self.assertEqual(goodbye[0].thread, thread.name, "the goodbye landed on a thread of its own")
