# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""The unread count and last-inbound stamp on a thread (Seityl/relay#8, #7).

`update_timestamps` compared the message's direction against "incoming",
but Relay Message.direction is a Select whose options are capitalised, so
the count never rose. Both tests are needed: comparing against the wrong
literal makes the branch inert, and a test asserting only the inbound case
would also pass if the count rose on every message.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime

ACCOUNT = "Unread Count Test Account"


class TestRelayThread(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		cls.contact = frappe.get_doc(
			{
				"doctype": "Relay Contact",
				"display_name": "Unread Count Test",
				"identifiers": [
					{
						"identifier_type": "Phone",
						"identifier_value": "15550008888",
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
				"channel": "Email",
			}
		).insert(ignore_permissions=True)

	def setUp(self):
		# IntegrationTestCase rolls back per class, not per test.
		frappe.db.savepoint("unread_count")
		self.addCleanup(frappe.db.rollback, save_point="unread_count")

	def _thread(self):
		return frappe.get_doc(
			{
				"doctype": "Relay Thread",
				"contact": self.contact.name,
				"account": self.account.name,
				"status": "Open",
			}
		).insert(ignore_permissions=True)

	def _message(self, thread, direction, status):
		return frappe.get_doc(
			{
				"doctype": "Relay Message",
				"thread": thread.name,
				"contact": self.contact.name,
				"account": self.account.name,
				"direction": direction,
				"status": status,
				"content_type": "text",
				"message_body": f"{direction} message",
				"message_id": frappe.generate_hash(length=12),
			}
		).insert(ignore_permissions=True)

	def _unread(self, thread):
		return frappe.db.get_value("Relay Thread", thread.name, "unread_count")

	def test_an_inbound_message_raises_the_unread_count(self):
		thread = self._thread()

		self._message(thread, "Incoming", "Delivered")
		self.assertEqual(self._unread(thread), 1)

		self._message(thread, "Incoming", "Delivered")
		self.assertEqual(self._unread(thread), 2)

	def test_an_outbound_message_does_not_raise_the_unread_count(self):
		thread = self._thread()
		self._message(thread, "Incoming", "Delivered")

		# Sent, not Pending: a Pending outbound message is queued for a
		# provider, which this test is not about.
		self._message(thread, "Outgoing", "Sent")

		self.assertEqual(self._unread(thread), 1)

	def test_an_inbound_message_records_when_the_thread_last_heard_from_the_contact(self):
		"""#7: the thread knows when its last inbound message arrived."""
		thread = self._thread()

		message = self._message(thread, "Incoming", "Delivered")
		stamp = frappe.db.get_value("Relay Thread", thread.name, "last_inbound_at")
		# A doc just inserted keeps creation as a string; the DB read is a
		# datetime. Compare like for like.
		self.assertEqual(stamp, get_datetime(message.creation))

	def test_an_outbound_message_does_not_move_last_inbound_at(self):
		"""#7: an outbound message does not open a session.

		Both branches are pinned: comparing against the wrong direction
		literal makes the stamp move on every message (#8's shape), and an
		inbound-only test would not catch that.
		"""
		thread = self._thread()
		inbound = self._message(thread, "Incoming", "Delivered")

		self._message(thread, "Outgoing", "Sent")

		stamp = frappe.db.get_value("Relay Thread", thread.name, "last_inbound_at")
		self.assertEqual(stamp, get_datetime(inbound.creation))

	def test_the_backfill_sets_last_inbound_at_from_the_threads_last_incoming_message(self):
		"""#7: the backfill patch stamps each thread from its own history.

		A thread with no incoming message keeps last_inbound_at unset: no
		session ever opened on it, and the backfill must not invent one.
		"""
		from relay.patches.backfill_last_inbound_at import execute as backfill

		thread = self._thread()
		first = self._message(thread, "Incoming", "Delivered")
		second = self._message(thread, "Incoming", "Delivered")
		# Order the two inbound messages explicitly: the backfill reads the
		# latest, and two inserts can land inside the same clock tick.
		frappe.db.set_value(
			"Relay Message", first.name, "creation", add_to_date(now_datetime(), days=-2)
		)
		self._message(thread, "Outgoing", "Sent")

		backfill()

		stamp = frappe.db.get_value("Relay Thread", thread.name, "last_inbound_at")
		self.assertEqual(stamp, get_datetime(second.creation))

		quiet = self._thread()
		backfill()
		self.assertIsNone(
			frappe.db.get_value("Relay Thread", quiet.name, "last_inbound_at")
		)

	def test_backfill_is_idempotent(self):
		"""#7: running the backfill twice changes nothing the second time."""
		from relay.patches.backfill_last_inbound_at import execute as backfill

		thread = self._thread()
		self._message(thread, "Incoming", "Delivered")
		backfill()
		stamp = frappe.db.get_value("Relay Thread", thread.name, "last_inbound_at")

		frappe.db.set_value(
			"Relay Thread", thread.name, "last_inbound_at", stamp, update_modified=False
		)
		backfill()
		self.assertEqual(frappe.db.get_value("Relay Thread", thread.name, "last_inbound_at"), stamp)
