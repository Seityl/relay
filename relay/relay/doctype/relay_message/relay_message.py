# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Relay Message controller."""

import frappe
from frappe.model.document import Document

from relay.relay.doctype.relay_contact.relay_contact import (
	RelayContact,
)
from relay.relay.doctype.relay_thread.relay_thread import (
	RelayThread,
)
from relay.queue.outbound_queue import queue_outgoing_message


class RelayMessage(Document):
	"""A single inbound or outbound message."""

	def validate(self):
		if not self.contact and self.thread:
			thread = frappe.get_doc("Relay Thread", self.thread)
			self.contact = thread.contact
		if not self.thread and self.contact:
			thread = RelayThread.get_or_create(self.contact, self.account)
			self.thread = thread.name

	def after_insert(self):
		if self.direction == "Outgoing" and self.status == "Pending":
			queue_outgoing_message(self)

		if self.thread:
			thread = frappe.get_doc("Relay Thread", self.thread)
			thread.update_timestamps(self.creation, self.direction)


@frappe.whitelist()
def send_message(
	phone_number: str,
	message_body: str = "",
	template: str = "",
	template_parameters: dict | None = None,
	content_type: str = "text",
	account: str = "",
	reference_doctype: str = "",
	reference_name: str = "",
	interactive_payload: dict | None = None,
	media_url: str = "",
) -> dict:
	"""Create an outgoing message and queue it for delivery.

	Either `message_body` (free-form) or `template` must be provided.
	"""
	if not phone_number:
		frappe.throw("Phone number is required")

	contact = RelayContact.get_or_create(phone_number)
	if not account:
		from relay.relay.doctype.relay_account.relay_account import (
			get_default_account,
		)

		account = get_default_account("outgoing")
		if not account:
			frappe.throw("No default outgoing account configured")

	thread = RelayThread.get_or_create(contact.name, account)

	message_type = "Freeform"
	if template:
		message_type = "Template"
	elif interactive_payload:
		message_type = "Interactive"

	doc = frappe.get_doc(
		{
			"doctype": "Relay Message",
			"thread": thread.name,
			"contact": contact.name,
			"account": account,
			"direction": "Outgoing",
			"status": "Pending",
			"message_type": message_type,
			"content_type": content_type,
			"message_body": message_body,
			"template": template,
			"template_parameters": frappe.as_json(template_parameters or {}),
			"interactive_payload": frappe.as_json(interactive_payload or {}),
			"media_url": media_url,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()

	return {
		"success": True,
		"message_id": doc.name,
		"thread_id": thread.name,
	}
