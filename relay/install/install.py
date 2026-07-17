"""Post-install setup for Relay."""

import frappe


def after_install():
	"""Seed default channel if it does not exist."""
	if not frappe.db.exists("Relay Channel", "Meta Cloud API"):
		frappe.get_doc(
			{
				"doctype": "Relay Channel",
				"channel_name": "Meta Cloud API",
				"provider": "Meta Cloud API",
				"enabled": 1,
				"handler_module": "relay.integrations.meta_cloud_api",
				"webhook_path": "/api/method/relay.webhooks.handler.receive",
				"supports_templates": 1,
				"supports_media": 1,
				"supports_interactive": 1,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
