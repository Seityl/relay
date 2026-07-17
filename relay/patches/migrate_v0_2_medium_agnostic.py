# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Migrate Relay from phone-centric to medium-agnostic data model.

- Copies legacy Relay Contact.phone_number into Relay Contact Identifier rows.
- Best-effort copies Relay Media rows into Relay Message Attachment rows.
"""

import frappe

CHANNELS = [
	{
		"channel_name": "Meta Cloud API",
		"provider": "Meta Cloud API",
		"enabled": 1,
		"adapter_class": "relay.integrations.meta_cloud_api.MetaCloudAPIAdapter",
		"handler_module": "relay.integrations.meta_cloud_api",
		"webhook_path": "/api/method/relay.webhooks.handler.receive",
		"supports_templates": 1,
		"supports_media": 1,
		"supports_interactive": 1,
	},
	{
		"channel_name": "Email",
		"provider": "Email",
		"enabled": 1,
		"adapter_class": "relay.integrations.email_adapter.EmailAdapter",
		"handler_module": "relay.integrations.email_adapter",
		"webhook_path": "/api/method/relay.webhooks.handler.receive",
		"supports_templates": 1,
		"supports_media": 0,
		"supports_interactive": 0,
	},
]


def execute():
	seed_channels()
	migrate_contact_phone_numbers()
	migrate_media_attachments()


def seed_channels():
	"""Seed default channels and ensure adapter_class is set on existing ones."""
	for channel_data in CHANNELS:
		if frappe.db.exists("Relay Channel", channel_data["channel_name"]):
			frappe.db.set_value(
				"Relay Channel",
				channel_data["channel_name"],
				{
					"adapter_class": channel_data["adapter_class"],
					"handler_module": channel_data["handler_module"],
					"supports_templates": channel_data["supports_templates"],
					"supports_media": channel_data["supports_media"],
					"supports_interactive": channel_data["supports_interactive"],
				},
			)
		else:
			frappe.get_doc({"doctype": "Relay Channel", **channel_data}).insert(
				ignore_permissions=True
			)

	frappe.db.commit()


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
		if not media.message:
			continue

		exists = frappe.db.exists(
			"Relay Message Attachment",
			{"parent": media.message, "file_url": media.file or media.file_url},
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
					"parent": media.message,
					"parenttype": "Relay Message",
					"parentfield": "attachments",
					"file_url": media.file or media.file_url,
					"file_name": file_name,
					"mime_type": media.mime_type,
					"caption": media.caption,
				}
			).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(title="Relay Media Migration Failed", message=frappe.get_traceback())

	frappe.db.commit()
