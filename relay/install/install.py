# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Post-install setup for Relay."""

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


def after_install():
	"""Seed default channels if they do not exist."""
	for channel_data in CHANNELS:
		if not frappe.db.exists("Relay Channel", channel_data["channel_name"]):
			frappe.get_doc({"doctype": "Relay Channel", **channel_data}).insert(
				ignore_permissions=True
			)

	frappe.db.commit()
