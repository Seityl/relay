# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Relay Thread controller."""

import frappe
from frappe import _
from frappe.model.document import Document


class RelayThread(Document):
	"""A conversation thread between the business and a contact."""

	def validate(self):
		if not self.account and self.contact:
			contact = frappe.get_doc("Relay Contact", self.contact)
			if contact.account:
				self.account = contact.account

	@staticmethod
	def get_or_create(contact: str, account: str | None = None, **kwargs) -> "RelayThread":
		"""Fetch an open thread for a contact or create one."""
		if not account:
			contact_doc = frappe.get_doc("Relay Contact", contact)
			account = contact_doc.account or get_default_account_for_contact()

		existing = frappe.db.get_value(
			"Relay Thread",
			{"contact": contact, "account": account, "status": "Open"},
			"name",
		)
		if existing:
			return frappe.get_doc("Relay Thread", existing)

		doc = frappe.get_doc(
			{
				"doctype": "Relay Thread",
				"contact": contact,
				"account": account,
				**kwargs,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	def update_timestamps(self, last_message_at: str, direction: str = "incoming"):
		"""Update thread metadata after a new message."""
		self.last_message_at = last_message_at
		if direction == "incoming":
			self.unread_count = (self.unread_count or 0) + 1
		self.save(ignore_permissions=True)


def get_default_account_for_contact() -> str | None:
	"""Return the default outgoing account."""
	from relay.relay.doctype.relay_account.relay_account import get_default_account

	return get_default_account("outgoing")


@frappe.whitelist()
def send_reply(
	thread: str,
	message_body: str = "",
	template: str = "",
	template_parameters: dict | None = None,
	content_type: str = "text",
	attachments: list | None = None,
) -> dict:
	"""Send a reply from a Relay Thread to its contact's primary identifier."""
	from relay.relay.doctype.relay_message.relay_message import send_message

	thread_doc = frappe.get_doc("Relay Thread", thread)
	contact = frappe.get_doc("Relay Contact", thread_doc.contact)
	primary = contact.get_primary_identifier()

	if not primary:
		frappe.throw(_("Contact has no primary identifier"))

	account = thread_doc.account or get_default_account_for_contact()
	if not account:
		frappe.throw(_("No outgoing account configured"))

	return send_message(
		recipient_type=primary.identifier_type,
		recipient_value=primary.identifier_value,
		message_body=message_body,
		template=template,
		template_parameters=template_parameters,
		content_type=content_type,
		account=account,
		reference_doctype="Relay Thread",
		reference_name=thread_doc.name,
		attachments=attachments,
	)
