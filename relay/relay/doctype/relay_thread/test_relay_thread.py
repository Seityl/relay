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

from relay.relay.doctype.relay_thread.relay_thread import RelayThread

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

		The migration's targets are threads that predate the field: their
		stamp is NULL, so the fixture clears it the way reality has it, and
		the patch must read the thread's last INCOMING message. A thread
		with no incoming message keeps last_inbound_at unset: no session
		ever opened on it, and the backfill must not invent one.
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
		outgoing = self._message(thread, "Outgoing", "Sent")
		# A pre-field thread: the field did not exist when these messages
		# were written, so nothing has stamped it yet.
		frappe.db.set_value("Relay Thread", thread.name, "last_inbound_at", None)

		backfill()

		stamp = frappe.db.get_value("Relay Thread", thread.name, "last_inbound_at")
		self.assertEqual(stamp, get_datetime(second.creation))
		self.assertNotEqual(stamp, get_datetime(outgoing.creation))

		# A thread with no incoming message: no session ever opened on it,
		# and the backfill must not invent one.
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

	# --- #22: one conversation is one thread until it is over -------------

	def test_an_inbound_continues_the_thread_a_previous_rule_moved_out_of_open(self):
		"""#22: Greeting moved the thread to Bot Handling; the next inbound
		still lands on it.

		The old lookup matched only status "Open", so once any rule had moved
		the thread, the very next message from the same contact opened a new
		thread and the conversation walked across threads, stranding unread
		counts on threads the conversation had left.
		"""
		thread = self._thread()
		frappe.db.set_value("Relay Thread", thread.name, "status", "Bot Handling")

		continued = RelayThread.get_or_create(self.contact.name, self.account.name)

		self.assertEqual(
			continued.name,
			thread.name,
			"a live thread the rules moved out of Open was abandoned; the "
			"conversation splits across threads and unread counts strand",
		)

	def test_a_terminal_thread_starts_a_fresh_conversation_on_the_next_inbound(self):
		"""#22 PIN: Resolved/Closed/Archived end the conversation.

		The lookup must stay narrow enough that a finished conversation is
		not resurrected: the desk Resolve button and the Stop rule both rely
		on the next inbound opening a fresh thread.
		"""
		for terminal in ("Resolved", "Closed", "Archived"):
			with self.subTest(terminal=terminal):
				thread = self._thread()
				frappe.db.set_value("Relay Thread", thread.name, "status", terminal)

				fresh = RelayThread.get_or_create(self.contact.name, self.account.name)

				self.assertNotEqual(
					fresh.name,
					thread.name,
					f"a {terminal} thread was continued; the conversation is over",
				)

	def test_a_contact_with_a_legacy_split_continues_their_newest_live_thread(self):
		"""#22: contacts whose conversation already split (pre-fix data) get
		the newest live thread, not whichever one happens to say Open."""
		older = self._thread()
		newer = self._thread()
		# Make the ordering unambiguous: two inserts can land in the same
		# clock tick (the backfill test hit the same wall).
		frappe.db.set_value(
			"Relay Thread", older.name, "creation", add_to_date(now_datetime(), days=-1),
			update_modified=False,
		)
		frappe.db.set_value("Relay Thread", newer.name, "status", "Bot Handling")

		continued = RelayThread.get_or_create(self.contact.name, self.account.name)

		self.assertEqual(
			continued.name,
			newer.name,
			"the older Open thread won; with the split data the conversation "
			"resumes on the wrong thread",
		)

	def test_when_the_newest_thread_is_terminal_an_older_live_thread_is_continued(self):
		"""#22: "live" decides, not "newest". A thread resolved five minutes
		ago does not outrank a thread the bot is still handling."""
		older = self._thread()
		newer = self._thread()
		frappe.db.set_value(
			"Relay Thread", older.name, "creation", add_to_date(now_datetime(), days=-1),
			update_modified=False,
		)
		frappe.db.set_value("Relay Thread", older.name, "status", "Bot Handling")
		frappe.db.set_value("Relay Thread", newer.name, "status", "Resolved")

		continued = RelayThread.get_or_create(self.contact.name, self.account.name)

		self.assertEqual(
			continued.name,
			older.name,
			"the resolved (newest) thread was continued; a finished "
			"conversation must not swallow the live one",
		)
