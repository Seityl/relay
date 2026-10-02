# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""The mark-read endpoint writes one column, not the whole thread (Seityl/relay#22).

`mark_thread_read` used to save the whole Relay Thread document it had
fetched. An inbound landing between that fetch and the save either collided
with the inbound's thread write (TimestampMismatchError -- a 500 to the desk
on the inbox's hottest path) or, once the inbound's write stopped colliding,
was written back over the fresh stamp, regressing last_message_at and
last_inbound_at. The endpoint wants ONE thing: unread_count = 0.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_datetime

ACCOUNT = "Mark Read Test Account"


class TestMarkThreadRead(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		cls.contact = frappe.get_doc(
			{
				"doctype": "Relay Contact",
				"display_name": "Mark Read Test",
				"identifiers": [
					{
						"identifier_type": "Phone",
						"identifier_value": "15550004444",
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
		frappe.db.savepoint("mark_read")
		self.addCleanup(frappe.db.rollback, save_point="mark_read")

	def _thread(self):
		return frappe.get_doc(
			{
				"doctype": "Relay Thread",
				"contact": self.contact.name,
				"account": self.account.name,
				"status": "Open",
			}
		).insert(ignore_permissions=True)

	def test_marking_a_thread_read_does_not_regress_an_inbound_that_landed_mid_request(self):
		"""#22: an inbound landing while the mark-read request runs loses
		nothing.

		The inbox calls mark_thread_read on every thread open. The overlap
		is simulated at the seam the race actually uses: the inbound's
		insert runs after the request has fetched its thread copy and
		before it writes.
		"""
		from relay.api.messages import mark_thread_read

		thread = self._thread()
		real_get_doc = frappe.get_doc
		landed = []
		state = {"done": False}

		def get_doc_then_inbound_lands(*args, **kwargs):
			doc = real_get_doc(*args, **kwargs)
			if (
				len(args) >= 2
				and args[0] == "Relay Thread"
				and args[1] == thread.name
				and not state["done"]
			):
				state["done"] = True
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
							"message_body": "a message that lands mid-request",
							"message_id": frappe.generate_hash(length=12),
						}
					).insert(ignore_permissions=True)
				)
			return doc

		with patch("relay.api.messages.frappe.get_doc", get_doc_then_inbound_lands):
			result = mark_thread_read(thread.name)

		self.assertTrue(result["success"])
		self.assertTrue(landed, "the inbound never landed, so this test exercised no overlap")
		self.assertEqual(
			frappe.db.get_value("Relay Thread", thread.name, "unread_count"),
			0,
			"the human marked the thread read; the badge must read zero",
		)
		self.assertEqual(
			get_datetime(frappe.db.get_value("Relay Thread", thread.name, "last_message_at")),
			get_datetime(landed[0].creation),
			"the mark-read regressed the stamp of the inbound that landed mid-request",
		)
		self.assertEqual(
			get_datetime(frappe.db.get_value("Relay Thread", thread.name, "last_inbound_at")),
			get_datetime(landed[0].creation),
			"the mark-read regressed the session stamp of the inbound that landed mid-request",
		)
