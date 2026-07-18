# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Opt-in / opt-out consent management with audit logging.

Contacts can opt out of messaging by sending stop keywords ("stop",
"unsubscribe", etc.). Once opted out, Relay refuses to send them outbound
messages until they explicitly opt back in ("start", "subscribe", etc.). Every
consent change is written to ``Relay Consent Log`` for compliance auditing.
"""

import frappe
from frappe import _


def process_stop_request(
	contact_doc,
	channel: str = "",
	account: str = "",
	message_doc=None,
	notes: str = "",
):
	"""Block a contact and record an opt-out event."""
	contact_doc = _resolve_contact(contact_doc)
	contact_doc.is_blocked = 1
	contact_doc.save(ignore_permissions=True)

	log_consent_event(
		"Opt-Out",
		contact_doc,
		channel=channel,
		account=account,
		source_message=_message_name(message_doc),
		source="Inbound",
		notes=notes or _("User requested to stop messaging"),
	)
	frappe.db.commit()


def process_opt_in(
	contact_doc,
	channel: str = "",
	account: str = "",
	message_doc=None,
	notes: str = "",
):
	"""Unblock a contact and record an opt-in event."""
	contact_doc = _resolve_contact(contact_doc)
	contact_doc.is_blocked = 0
	contact_doc.save(ignore_permissions=True)

	log_consent_event(
		"Opt-In",
		contact_doc,
		channel=channel,
		account=account,
		source_message=_message_name(message_doc),
		source="Inbound",
		notes=notes or _("User requested to resubscribe"),
	)
	frappe.db.commit()


def check_can_send(contact_doc, raise_error: bool = False) -> bool:
	"""Return True if Relay is allowed to message the contact."""
	contact_doc = _resolve_contact(contact_doc)
	allowed = not bool(contact_doc.is_blocked)

	if not allowed and raise_error:
		frappe.throw(_("This contact has opted out of messaging."))

	return allowed


def log_consent_event(
	event_type: str,
	contact_doc,
	channel: str = "",
	account: str = "",
	source_message: str = "",
	source: str = "Manual",
	notes: str = "",
):
	"""Write a consent change to the audit log."""
	contact_doc = _resolve_contact(contact_doc)

	frappe.get_doc(
		{
			"doctype": "Relay Consent Log",
			"event_type": event_type,
			"contact": contact_doc.name,
			"channel": channel,
			"account": account,
			"source_message": source_message,
			"source": source,
			"notes": notes,
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()


def _resolve_contact(contact_doc):
	"""Ensure we have a Relay Contact document."""
	if isinstance(contact_doc, str):
		return frappe.get_doc("Relay Contact", contact_doc)
	return contact_doc


def _message_name(message_doc):
	"""Return a message document name or empty string."""
	if not message_doc:
		return ""
	if isinstance(message_doc, str):
		return message_doc
	return message_doc.name
