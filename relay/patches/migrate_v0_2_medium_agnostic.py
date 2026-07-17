# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Migrate Relay from phone-centric to medium-agnostic data model.

- Copies legacy Relay Contact.phone_number into Relay Contact Identifier rows.
- Best-effort copies Relay Media rows into Relay Message Attachment rows.
"""

import frappe


def execute():
	migrate_contact_phone_numbers()
	migrate_media_attachments()


def migrate_contact_phone_numbers():
	"""Create Phone identifiers for contacts that still have a phone_number value."""
	contacts = frappe.get_all(
		"Relay Contact",
		filters={"phone_number": ["is", "set"]},
		fields=["name", "phone_number"],
	)

	for contact in contacts:
		phone = contact.phone_number.lstrip("+").strip()
		if not phone:
			continue

		exists = frappe.db.exists(
			"Relay Contact Identifier",
			{"parent": contact.name, "identifier_type": "Phone", "identifier_value": phone},
		)
		if exists:
			continue

		frappe.get_doc(
			{
				"doctype": "Relay Contact Identifier",
				"parent": contact.name,
				"parenttype": "Relay Contact",
				"parentfield": "identifiers",
				"identifier_type": "Phone",
				"identifier_value": phone,
				"is_primary": 1,
			}
		).insert(ignore_permissions=True)

	frappe.db.commit()


def migrate_media_attachments():
	"""Best-effort copy Relay Media rows into Relay Message Attachment rows."""
	if not frappe.db.table_exists("tabRelay Media") or not frappe.db.table_exists(
		"tabRelay Message Attachment"
	):
		return

	media_rows = frappe.get_all(
		"Relay Media",
		fields=["name", "message", "file", "file_url", "mime_type", "caption"],
	)

	for media in media_rows:
		exists = frappe.db.exists(
			"Relay Message Attachment",
			{"message": media.message, "file_url": media.file or media.file_url},
		)
		if exists:
			continue

		file_name = ""
		if media.file:
			file_name = media.file.split("/")[-1]
		elif media.file_url:
			file_name = media.file_url.split("/")[-1]

		try:
			frappe.get_doc(
				{
					"doctype": "Relay Message Attachment",
					"message": media.message,
					"file_url": media.file or media.file_url,
					"file_name": file_name,
					"mime_type": media.mime_type,
					"caption": media.caption,
				}
			).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(title="Relay Media Migration Failed", message=frappe.get_traceback())

	frappe.db.commit()
