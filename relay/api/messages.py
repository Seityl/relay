# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Public REST API for the Relay app."""

import frappe

from relay.relay.doctype.relay_message.relay_message import send_message


@frappe.whitelist()
def send(
	phone_number: str,
	message_body: str = "",
	template: str = "",
	template_parameters: dict | None = None,
	content_type: str = "text",
	account: str = "",
	reference_doctype: str = "",
	reference_name: str = "",
) -> dict:
	"""Send a free-form or template message to a phone number."""
	return send_message(
		phone_number=phone_number,
		message_body=message_body,
		template=template,
		template_parameters=template_parameters,
		content_type=content_type,
		account=account,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)


@frappe.whitelist()
def get_threads(
	search: str = "",
	status: str = "",
	limit: int = 50,
	offset: int = 0,
) -> list[dict]:
	"""Return conversation threads for the inbox."""
	filters = {}
	if status:
		filters["status"] = status

	threads = frappe.get_all(
		"Relay Thread",
		filters=filters,
		fields=[
			"name",
			"contact",
			"account",
			"status",
			"last_message_at",
			"unread_count",
			"subject",
		],
		limit=limit,
		start=offset,
		order_by="last_message_at desc",
	)

	for thread in threads:
		contact = frappe.db.get_value(
			"Relay Contact",
			thread.contact,
			["display_name", "phone_number", "profile_name"],
			as_dict=True,
		)
		thread["contact_name"] = (
			contact.display_name or contact.profile_name or contact.phone_number
		)
		thread["phone_number"] = contact.phone_number

	return threads


@frappe.whitelist()
def get_messages(
	thread: str,
	limit: int = 50,
	before: str = "",
) -> list[dict]:
	"""Return messages in a thread."""
	filters = {"thread": thread}
	if before:
		filters["creation"] = ["<", before]

	return frappe.get_all(
		"Relay Message",
		filters=filters,
		fields=[
			"name",
			"direction",
			"status",
			"content_type",
			"message_body",
			"message_id",
			"media",
			"sent_at",
			"delivered_at",
			"read_at",
			"creation",
		],
		limit=limit,
		order_by="creation desc",
	)


@frappe.whitelist()
def mark_thread_read(thread: str) -> dict:
	"""Reset unread count on a thread."""
	thread_doc = frappe.get_doc("Relay Thread", thread)
	thread_doc.unread_count = 0
	thread_doc.save(ignore_permissions=True)
	return {"success": True}


@frappe.whitelist()
def get_templates(
	status: str = "Approved",
	language_code: str = "",
) -> list[dict]:
	"""Return available message templates."""
	filters = {}
	if status:
		filters["status"] = status
	if language_code:
		filters["language_code"] = language_code

	return frappe.get_all(
		"Relay Template",
		filters=filters,
		fields=[
			"name",
			"template_name",
			"actual_name",
			"language_code",
			"category",
			"status",
		],
	)
