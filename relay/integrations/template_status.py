# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""A template's approval status follows what the provider says (#6).

Two ways in: Meta pushes a webhook (handler -> `apply_template_status`);
Twilio has no such webhook, so `refresh_template_statuses` asks it every
hour. Both arrive here already in relay's vocabulary, and both write through
the document, so the Select is validated and the change has a Version.
"""

import frappe

from relay.integrations.base_adapter import BaseChannelAdapter, TemplateStatusEvent
from relay.integrations.registry import get_adapter, get_registered_adapters


def apply_template_status(event: TemplateStatusEvent) -> list[str]:
	"""Set the status on every Relay Template with this provider id.

	Returns the names whose status changed. An event with no status is one
	the adapter chose not to act on, and changes nothing. A template that
	cannot be saved is logged under its name and does not stop the others --
	on the webhook path it would otherwise fail every event in the payload.
	"""
	if not event.provider_template_id or not event.status:
		return []

	changed = []
	for row in frappe.get_all(
		"Relay Template",
		filters={"provider_template_id": event.provider_template_id},
		fields=["name", "provider_template_id"],
	):
		# The column's collation ignores case and trailing spaces; an id does not.
		if row.provider_template_id != event.provider_template_id:
			continue
		doc = frappe.get_doc("Relay Template", row.name)
		if doc.status == event.status:
			continue
		frappe.db.savepoint("template_status_apply")
		try:
			doc.status = event.status
			doc.save(ignore_permissions=True)
		except Exception:
			frappe.db.rollback(save_point="template_status_apply")
			frappe.log_error(
				title=f"Relay Template Status Not Applied: {row.name}",
				message=frappe.get_traceback(),
			)
			continue
		changed.append(row.name)
	return changed


def _polled_providers() -> set[str]:
	"""Providers whose adapter asks for template status rather than being told."""
	return {
		provider
		for provider, adapter_class in get_registered_adapters().items()
		if adapter_class.fetch_template_status is not BaseChannelAdapter.fetch_template_status
	}


def _account_for(channel: str) -> str | None:
	"""The Active account whose credentials are used for this channel's templates."""
	accounts = frappe.get_all(
		"Relay Account",
		filters={"channel": channel, "status": "Active"},
		order_by="is_default_outgoing desc, creation asc",
		pluck="name",
		limit=1,
	)
	return accounts[0] if accounts else None


def refresh_template_statuses():
	"""Hourly: ask each polled provider about every template relay holds an id for.

	Approved templates are asked about too -- WhatsApp pauses and disables
	templates after approval. One template's failure is logged under its
	name and does not stop the next.
	"""
	polled = _polled_providers()
	channels = {
		row.name: row.provider
		for row in frappe.get_all("Relay Channel", fields=["name", "provider"])
		if row.provider in polled
	}
	if not channels:
		return

	templates = frappe.get_all(
		"Relay Template",
		filters={"channel": ["in", list(channels)], "provider_template_id": ["is", "set"]},
		fields=["name", "channel"],
		order_by="name asc",
	)
	for row in templates:
		frappe.db.savepoint("template_status_row")
		try:
			account_name = _account_for(row.channel)
			if not account_name:
				frappe.throw(
					f"Relay Template {row.name} is on channel {row.channel}, which has no "
					"Active Relay Account, so there are no credentials to ask with."
				)
			adapter = get_adapter(channels[row.channel], frappe.get_doc("Relay Account", account_name))
			event = adapter.fetch_template_status(frappe.get_doc("Relay Template", row.name))
			if event:
				apply_template_status(event)
			frappe.db.commit()
		except Exception:
			# Undo this row only; a bare rollback would also discard work
			# the caller has not committed yet.
			frappe.db.rollback(save_point="template_status_row")
			frappe.log_error(
				title=f"Relay Template Status Not Refreshed: {row.name}",
				message=frappe.get_traceback(),
			)
