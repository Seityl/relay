# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Post-install setup for Relay."""

import frappe


DEFAULT_AUTO_REPLY_RULES = [
	{
		"rule_name": "Stop / Unsubscribe",
		"enabled": 1,
		"priority": 10,
		"match_type": "Intent",
		"intent": "Stop",
		"response_type": "Freeform",
		"response_body": "You have been unsubscribed from our messaging service. Reply START at any time to resubscribe.",
		"set_thread_status": "Closed",
		"add_tags": "opt-out",
	},
	{
		"rule_name": "Start / Resubscribe",
		"enabled": 1,
		"priority": 20,
		"match_type": "Intent",
		"intent": "Start",
		"response_type": "Freeform",
		"response_body": "Welcome back! How can we help you today?",
		"set_thread_status": "Open",
		"add_tags": "opt-in",
	},
	{
		"rule_name": "Greeting",
		"enabled": 1,
		"priority": 100,
		"match_type": "Intent",
		"intent": "Greeting",
		"response_type": "Freeform",
		"response_body": "Hello! Welcome to our pharmacy. How can we assist you today?",
		"set_thread_status": "Bot Handling",
		"add_tags": "greeting",
	},
	{
		"rule_name": "Refill Request",
		"enabled": 1,
		"priority": 110,
		"match_type": "Intent",
		"intent": "Refill",
		"response_type": "Freeform",
		"response_body": "We can help with your refill. A team member will review your request shortly.",
		"set_thread_status": "Awaiting Agent",
		"add_tags": "refill",
	},
	{
		"rule_name": "Prescription Question",
		"enabled": 1,
		"priority": 120,
		"match_type": "Intent",
		"intent": "Prescription",
		"response_type": "Freeform",
		"response_body": "We can assist with your prescription. A team member will be with you shortly.",
		"set_thread_status": "Awaiting Agent",
		"add_tags": "prescription",
	},
	{
		"rule_name": "Delivery Tracking",
		"enabled": 1,
		"priority": 130,
		"match_type": "Intent",
		"intent": "Delivery",
		"response_type": "Freeform",
		"response_body": "Let us check your delivery status. A team member will update you shortly.",
		"set_thread_status": "Awaiting Agent",
		"add_tags": "delivery",
	},
	{
		"rule_name": "Help Request",
		"enabled": 1,
		"priority": 140,
		"match_type": "Intent",
		"intent": "Help",
		"response_type": "Freeform",
		"response_body": "We're here to help. A team member will assist you shortly.",
		"set_thread_status": "Awaiting Agent",
		"add_tags": "support",
	},
	{
		"rule_name": "Complaint / Issue",
		"enabled": 1,
		"priority": 150,
		"match_type": "Intent",
		"intent": "Complaint",
		"response_type": "Freeform",
		"response_body": "We're sorry to hear that. A manager will look into this promptly.",
		"set_thread_status": "Escalated",
		"add_tags": "complaint",
	},
	{
		"rule_name": "Fallback",
		"enabled": 1,
		"priority": 1000,
		"match_type": "Always",
		"response_type": "Freeform",
		"response_body": "Sorry, we didn't understand. Reply HELP for assistance or STOP to unsubscribe.",
		"set_thread_status": "Awaiting Agent",
		"fallback_when_no_match": 1,
	},
]


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
	"""Seed default channels, roles and auto-reply rules if they do not exist."""
	create_roles()

	for channel_data in CHANNELS:
		if not frappe.db.exists("Relay Channel", channel_data["channel_name"]):
			frappe.get_doc({"doctype": "Relay Channel", **channel_data}).insert(
				ignore_permissions=True
			)

	seed_default_auto_reply_rules()
	frappe.db.commit()


def create_roles():
	"""Create Relay roles if they do not exist."""
	for role_name in ("Relay User", "Relay Manager"):
		if not frappe.db.exists("Role", role_name):
			frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(
				ignore_permissions=True
			)


def seed_default_auto_reply_rules():
	"""Create default auto-reply rules when none exist."""
	for rule_data in DEFAULT_AUTO_REPLY_RULES:
		if not frappe.db.exists("Relay Auto Reply Rule", rule_data["rule_name"]):
			frappe.get_doc({"doctype": "Relay Auto Reply Rule", **rule_data}).insert(
				ignore_permissions=True
			)
	frappe.db.commit()
