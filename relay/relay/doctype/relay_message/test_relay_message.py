# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""send_message's thread pin is an ownership boundary (Seityl/relay#22).

`thread` lets a caller pin a message to an existing conversation. It is
reachable through a whitelisted function, so it must not become a way to
write a message onto somebody else's thread: the pin is refused unless the
thread belongs to the recipient's contact and to the sending account.
"""

import frappe
from frappe.tests import IntegrationTestCase

ACCOUNT = "Message Pin Test Account"


class TestRelayMessage(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		cls.contact = frappe.get_doc(
			{
				"doctype": "Relay Contact",
				"display_name": "Message Pin Owner",
				"identifiers": [
					{
						"identifier_type": "Phone",
						"identifier_value": "15550002222",
						"is_primary": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		cls.other_contact = frappe.get_doc(
			{
				"doctype": "Relay Contact",
				"display_name": "Message Pin Stranger",
				"identifiers": [
					{
						"identifier_type": "Phone",
						"identifier_value": "15550003333",
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
		frappe.db.savepoint("message_pin")
		self.addCleanup(frappe.db.rollback, save_point="message_pin")

	def _thread(self, contact):
		return frappe.get_doc(
			{
				"doctype": "Relay Thread",
				"contact": contact.name,
				"account": self.account.name,
				"status": "Open",
			}
		).insert(ignore_permissions=True)

	def test_the_relay_message_carries_a_handled_by_field(self):
		"""#25 PIN: the claim contract's field exists, is a read-only Data
		field, and its description states the contract. Green by design
		after the schema lands; it guards against accidental removal."""
		field = frappe.get_meta("Relay Message").get_field("handled_by")

		self.assertIsNotNone(field, "the Relay Message claim field handled_by is gone")
		self.assertEqual(field.fieldtype, "Data", "handled_by must stay a Data field")
		self.assertTrue(field.read_only, "handled_by must be read-only on the desk")

	def test_a_reply_pinned_to_a_thread_from_another_contact_is_refused(self):
		"""#22: pinning a message to a thread that belongs to someone else
		is refused, naming the thread and both contacts."""
		from relay.relay.doctype.relay_message.relay_message import send_message

		owners_thread = self._thread(self.contact)

		with patch_commit(), self.assertRaises(frappe.ValidationError) as ctx:
			send_message(
				recipient_type="Phone",
				recipient_value="15550003333",
				message_body="a message for the stranger",
				account=self.account.name,
				thread=owners_thread.name,
			)

		self.assertIn(owners_thread.name, str(ctx.exception))
		self.assertIn(self.contact.name, str(ctx.exception))
		# Nothing was written onto the owner's thread.
		self.assertEqual(
			frappe.db.count("Relay Message", {"thread": owners_thread.name}),
			0,
			"the refused pin left a message on the owner's thread",
		)

	def test_a_reply_pinned_to_a_thread_on_another_account_is_refused(self):
		"""#22: the pin must also carry the account the thread is on."""
		from relay.relay.doctype.relay_message.relay_message import send_message

		thread = self._thread(self.contact)
		other_account = frappe.get_doc(
			{
				"doctype": "Relay Account",
				"account_name": "Message Pin Other Account",
				"status": "Active",
				"channel": "Email",
			}
		).insert(ignore_permissions=True)

		with patch_commit(), self.assertRaises(frappe.ValidationError) as ctx:
			send_message(
				recipient_type="Phone",
				recipient_value="15550002222",
				message_body="a message via the wrong account",
				account=other_account.name,
				thread=thread.name,
			)

		self.assertIn(other_account.name, str(ctx.exception))


def patch_commit():
	"""send_message commits; a commit here would end the class transaction."""
	from unittest.mock import patch

	return patch("frappe.db.commit")
