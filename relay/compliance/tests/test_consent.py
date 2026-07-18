# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Tests for Relay consent management."""

import frappe
from frappe.tests.utils import FrappeTestCase

from relay.compliance.consent import check_can_send, process_opt_in, process_stop_request


class TestConsent(FrappeTestCase):
	"""Exercise opt-in / opt-out logic with the database."""

	def setUp(self):
		self.contact = frappe.get_doc(
			{
				"doctype": "Relay Contact",
				"display_name": "Consent Test",
				"identifiers": [
					{
						"identifier_type": "Phone",
						"identifier_value": "15551234567",
						"is_primary": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

	def test_process_stop_request_blocks_contact(self):
		process_stop_request(self.contact, notes="Test opt-out")
		self.contact.reload()
		self.assertTrue(self.contact.is_blocked)
		self.assertTrue(
			frappe.db.exists("Relay Consent Log", {"contact": self.contact.name, "event_type": "Opt-Out"})
		)

	def test_process_opt_in_unblocks_contact(self):
		self.contact.is_blocked = 1
		self.contact.save(ignore_permissions=True)

		process_opt_in(self.contact, notes="Test opt-in")
		self.contact.reload()
		self.assertFalse(self.contact.is_blocked)
		self.assertTrue(
			frappe.db.exists("Relay Consent Log", {"contact": self.contact.name, "event_type": "Opt-In"})
		)

	def test_check_can_send_respects_block(self):
		self.assertTrue(check_can_send(self.contact))
		self.contact.is_blocked = 1
		self.contact.save(ignore_permissions=True)
		self.assertFalse(check_can_send(self.contact))

	def tearDown(self):
		frappe.db.delete("Relay Consent Log", {"contact": self.contact.name})
		frappe.db.delete("Relay Contact", {"name": self.contact.name})
