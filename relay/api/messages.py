# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Public REST API for the Relay app."""

import frappe

from relay.relay.doctype.relay_message.relay_message import send_message


@frappe.whitelist()
def send(
	phone_number: str = "",
	recipient_type: str = "",
	recipient_value: str = "",
	message_body: str = "",
	subject: str = "",
	html_body: str = "",
	recipient_data: dict | None = None,
	attachments: list | None = None,
	template: str = "",
	template_parameters: dict | None = None,
	content_type: str = "text",
	account: str = "",
	reference_doctype: str = "",
	reference_name: str = "",
) -> dict:
	"""Send a free-form or template message to a recipient.

	Backward-compatible: if only `phone_number` is provided, it is treated as a Phone recipient.
	"""
	if phone_number and not recipient_value:
		recipient_type = "Phone"
		recipient_value = phone_number

	return send_message(
		recipient_type=recipient_type,
		recipient_value=recipient_value,
		message_body=message_body,
		subject=subject,
		html_body=html_body,
		recipient_data=recipient_data,
		attachments=attachments,
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
	unread_only: bool = False,
	prescription_only: bool = False,
	limit: int = 50,
	offset: int = 0,
) -> list[dict]:
	"""Return conversation threads for the inbox."""
	filters = {}
	if status:
		filters["status"] = status
	if unread_only:
		filters["unread_count"] = [">", 0]
	if prescription_only:
		filters["reference_doctype"] = "RxFlow Prescription"

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
			"reference_doctype",
			"reference_name",
		],
		limit=limit,
		start=offset,
		order_by="last_message_at desc",
	)

	results = []
	search_lower = (search or "").lower()
	for thread in threads:
		contact = frappe.get_doc("Relay Contact", thread.contact)
		primary = contact.get_primary_identifier()
		thread["contact_name"] = contact.display_name or contact.profile_name or (
			primary.identifier_value if primary else ""
		)
		thread["phone_number"] = contact.get_identifier("Phone").identifier_value if contact.get_identifier("Phone") else None
		thread["email"] = contact.get_identifier("Email").identifier_value if contact.get_identifier("Email") else None
		thread["last_message"] = _last_message_preview(thread.name)

		if search_lower:
			searchable = (
				(thread["contact_name"] or "")
				+ " "
				+ (thread["phone_number"] or "")
				+ " "
				+ (thread["email"] or "")
				+ " "
				+ (thread["subject"] or "")
			).lower()
			if search_lower not in searchable:
				continue

		results.append(thread)

	return results


@frappe.whitelist()
def get_messages(
	thread: str,
	limit: int = 50,
	before: str = "",
) -> list[dict]:
	"""Return messages in a thread, including attachments."""
	filters = {"thread": thread}
	if before:
		filters["creation"] = ["<", before]

	messages = frappe.get_all(
		"Relay Message",
		filters=filters,
		fields=[
			"name",
			"direction",
			"status",
			"content_type",
			"message_body",
			"html_body",
			"message_id",
			"media",
			"media_url",
			"sent_at",
			"delivered_at",
			"read_at",
			"creation",
			"reference_doctype",
			"reference_name",
		],
		limit=limit,
		order_by="creation desc",
	)

	for message in messages:
		message["attachments"] = [
			{
				"file_url": a.file_url,
				"file_name": a.file_name,
				"mime_type": a.mime_type,
				"caption": a.caption,
			}
			for a in frappe.get_all(
				"Relay Message Attachment",
				filters={"parent": message.name},
				fields=["file_url", "file_name", "mime_type", "caption"],
			)
		]

	return messages


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


@frappe.whitelist()
def get_template_parameters(template: str) -> list[dict]:
	"""Return parameters for a template so the UI can render input fields."""
	if not frappe.db.exists("Relay Template", template):
		return []

	return frappe.get_all(
		"Relay Template Parameter",
		filters={"parent": template},
		fields=["parameter_name", "parameter_index", "sample_value"],
		order_by="parameter_index asc",
	)


def _last_message_preview(thread: str) -> str:
	"""Return a short preview of the most recent message in a thread."""
	message = frappe.get_all(
		"Relay Message",
		filters={"thread": thread},
		fields=["message_body", "content_type"],
		limit=1,
		order_by="creation desc",
	)
	if not message:
		return ""
	body = message[0].message_body or ""
	if message[0].content_type in ("image", "document", "audio", "video"):
		return f"[{message[0].content_type.capitalize()}] {body}"
	return body[:80]
