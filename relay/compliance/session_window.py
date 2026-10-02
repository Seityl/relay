# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""The WhatsApp customer-service session window (Seityl/relay#7).

WhatsApp allows freeform business messages only within 24 hours of the
customer's last inbound message; outside that window only an approved
template may be sent, and the provider rejects anything else (Twilio 63016,
Meta 131047). Relay used to decide freeform-vs-template from the caller's
arguments alone, so it queued messages the provider was certain to refuse --
five billable calls on the retry backoff for a deterministic outcome.

This check is consulted BEFORE queueing, so the caller gets the refusal and
its reason instead of a doomed queue row.

The window is anchored on the CONTACT, not on one thread: WhatsApp's session
belongs to the (customer, business phone number) pair, which is relay's
(contact, account). Relay's threads can split -- with the default auto-reply
rules an inbound may move its thread out of Open and the reply opens a new
one (recorded on Seityl/rxflow#111 from the #15 verify) -- so a check that
read only the reply's own thread would refuse a reply to a message that
arrived seconds ago. `Relay Thread.last_inbound_at` still records the same
fact per thread for the inbox; the decision reads the contact's history.
"""

import frappe
from frappe import _

from relay.integrations.base_adapter import PermanentRejection

#: Providers whose channels enforce WhatsApp's 24-hour customer-service
#: window. relay's Twilio adapter sends every message with a `whatsapp:`
#: prefix (twilio_adapter._scheme), and Meta Cloud API is WhatsApp only, so
#: both enforce it. Email has no such window.
SESSION_WINDOW_HOURS = {
	"Twilio": 24,
	"Meta Cloud API": 24,
}


def window_hours_for_provider(provider: str) -> int | None:
	"""Hours in the provider's session window, or None if it has none."""
	return SESSION_WINDOW_HOURS.get(provider)


def _provider_of(account: str) -> str | None:
	channel = frappe.db.get_value("Relay Account", account, "channel")
	if not channel:
		return None
	return frappe.db.get_value("Relay Channel", channel, "provider")


def last_inbound_on(contact: str, account: str) -> dict | None:
	"""The contact's latest incoming message on this account, or None.

	Read from Relay Message rather than a thread field so the decision is
	immune to thread splitting. Returns the message's thread and creation.
	"""
	rows = frappe.get_all(
		"Relay Message",
		filters={"contact": contact, "account": account, "direction": "Incoming"},
		fields=["thread", "creation"],
		order_by="creation desc",
		limit=1,
	)
	return rows[0] if rows else None


def check_session_window(contact: str, account: str, message_type: str) -> None:
	"""Refuse a freeform send outside the provider's session window.

	Raises PermanentRejection (a frappe.ValidationError) naming the thread
	the last inbound lives on and the age of that inbound. Template and
	Interactive sends pass: the window's whole purpose is that an approved
	template may go outside it, and #7 scopes this check to freeform.
	"""
	if message_type != "Freeform":
		return

	hours = window_hours_for_provider(_provider_of(account) or "")
	if not hours:
		return

	last_inbound = last_inbound_on(contact, account)
	if not last_inbound:
		frappe.throw(
			_("{0} has never sent an inbound message to {1}, so no {2}-hour "
				"session is open and only an approved template may be sent")
			.format(contact, account, hours),
			PermanentRejection,
		)

	age = frappe.utils.time_diff_in_seconds(
		frappe.utils.now_datetime(), frappe.utils.get_datetime(last_inbound["creation"])
	)
	if age <= hours * 3600:
		return

	frappe.throw(
		_("the last inbound message on thread {0} was {1}, so only an approved "
			"template may be sent to {2}")
		.format(
			last_inbound["thread"],
			frappe.utils.pretty_date(last_inbound["creation"]),
			contact,
		),
		PermanentRejection,
	)
