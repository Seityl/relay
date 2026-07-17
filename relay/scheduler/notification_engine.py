# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""DocType-event and scheduled notification engine."""

import json

import frappe
from frappe.utils.safe_exec import get_safe_globals, safe_exec

from relay.relay.doctype.relay_account.relay_account import get_default_account
from relay.relay.doctype.relay_message.relay_message import send_message


EVENT_MAP = {
	"after_insert": "After Insert",
	"on_update": "On Update",
	"after_submit": "After Submit",
	"after_cancel": "After Cancel",
	"after_delete": "After Delete",
}


def run_doc_event_notification(doc, event: str):
	"""Trigger notifications for a DocType event."""
	if event not in EVENT_MAP:
		return

	notifications = frappe.get_all(
		"Relay Notification",
		filters={
			"enabled": 1,
			"reference_doctype": doc.doctype,
			"trigger_event": EVENT_MAP[event],
		},
		fields=["name"],
	)

	for notification in notifications:
		frappe.enqueue(
			method="relay.scheduler.notification_engine.send_notification",
			queue="short",
			notification_name=notification.name,
			doctype=doc.doctype,
			docname=doc.name,
		)


@frappe.whitelist()
def send_notification(notification_name: str, doctype: str, docname: str):
	"""Send a single notification."""
	notification = frappe.get_doc("Relay Notification", notification_name)
	if not notification.enabled:
		return

	ref_doc = frappe.get_doc(doctype, docname)

	if notification.condition:
		if not _evaluate_condition(notification.condition, ref_doc):
			return

	phone = _resolve_phone(ref_doc, notification.phone_field)
	if not phone:
		frappe.log_error(
			title="Relay Notification Failed",
			message=f"No phone number resolved for {doctype} {docname}",
		)
		return

	parameters = {}
	for param in notification.parameters:
		if param.static_value:
			value = param.static_value
		elif param.field_name:
			value = ref_doc.get_formatted(param.field_name)
		else:
			value = ""
		parameters[param.parameter_name] = value

	account = notification.account or get_default_account("outgoing")
	if not account:
		frappe.log_error(
			title="Relay Notification Failed",
			message=f"No outgoing account for notification {notification_name}",
		)
		return

	try:
		result = send_message(
			phone_number=phone,
			template=notification.template,
			template_parameters=parameters,
			account=account,
			reference_doctype=doctype,
			reference_name=docname,
		)

		if notification.set_property_after_alert:
			frappe.db.set_value(
				doctype,
				docname,
				notification.set_property_after_alert,
				notification.property_value,
			)

		return result
	except Exception:
		frappe.log_error(title=f"Relay Notification Failed: {notification_name}")


def run_scheduled_notifications(frequency: str):
	"""Run notifications configured for a scheduler frequency."""
	notifications = frappe.get_all(
		"Relay Notification",
		filters={"enabled": 1, "event_frequency": frequency},
		fields=["name", "reference_doctype"],
	)

	for notification in notifications:
		docs = frappe.get_all(notification.reference_doctype, fields=["name"])
		for doc in docs:
			frappe.enqueue(
				method="relay.scheduler.notification_engine.send_notification",
				queue="long",
				notification_name=notification.name,
				doctype=notification.reference_doctype,
				docname=doc.name,
			)


def hourly():
	run_scheduled_notifications("Hourly")


def hourly_long():
	run_scheduled_notifications("Hourly Long")


def daily():
	run_scheduled_notifications("Daily")


def daily_long():
	run_scheduled_notifications("Daily Long")


def weekly():
	run_scheduled_notifications("Weekly")


def weekly_long():
	run_scheduled_notifications("Weekly Long")


def monthly():
	run_scheduled_notifications("Monthly")


def monthly_long():
	run_scheduled_notifications("Monthly Long")


def yearly():
	run_scheduled_notifications("Yearly")


def _evaluate_condition(condition: str, doc) -> bool:
	"""Evaluate a Python expression condition against a document."""
	try:
		return bool(safe_exec(condition, get_safe_globals(), {"doc": doc.as_dict()}))
	except Exception:
		return False


def _resolve_phone(doc, phone_field: str) -> str | None:
	"""Resolve phone number from a document field."""
	if phone_field:
		value = doc.get(phone_field)
		if value:
			return str(value)

	# Common fallbacks
	for field in ("mobile_no", "phone", "mobile_number", "whatsapp_number", "contact_number"):
		value = doc.get(field)
		if value:
			return str(value)

	return None
