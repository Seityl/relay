# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""The unread count on a thread (Seityl/relay#8).

`update_timestamps` compared the message's direction against "incoming",
but Relay Message.direction is a Select whose options are capitalised, so
the count never rose. Both tests are needed: comparing against the wrong
literal makes the branch inert, and a test asserting only the inbound case
would also pass if the count rose on every message.
"""

import frappe
from frappe.tests import IntegrationTestCase

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
