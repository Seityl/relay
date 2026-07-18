# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Adapter for sending email via Frappe's built-in email utilities."""

import socket

import frappe

from relay.integrations.base_adapter import (
	BaseChannelAdapter,
	InboundPayload,
	NormalizedRecipient,
)
from relay.integrations.registry import register


class EmailAdapter(BaseChannelAdapter):
	"""Send outbound email through Frappe's email layer."""

	def send(self, queue_doc) -> str:
		"""Send an email and return a generated message id."""
		recipients = self._resolve_recipients(queue_doc)
		if not recipients:
			frappe.throw("No email recipients resolved")

		subject = queue_doc.subject or ""
		message = queue_doc.html_body or queue_doc.message_body or ""
		sender = self.account.default_sender_address or None

		email_message_id = self._generate_message_id(queue_doc)
		in_reply_to = self._get_in_reply_to(queue_doc.thread)

		frappe.sendmail(
			recipients=recipients,
			subject=subject,
			message=message,
			sender=sender,
			reference_doctype=queue_doc.reference_doctype,
			reference_name=queue_doc.reference_name,
			message_id=email_message_id,
			in_reply_to=in_reply_to,
		)

		self._store_email_message_id(queue_doc, email_message_id)
		return email_message_id

	def _generate_message_id(self, queue_doc) -> str:
		"""Generate a stable RFC-2822 style Message-ID for this queue entry."""
		host = frappe.local.site or socket.gethostname()
		return f"<relay-{queue_doc.name}@{host}>"

	def _get_in_reply_to(self, thread: str) -> str:
		"""Return the Message-ID of the most recent inbound email in the thread."""
		if not thread:
			return ""
		inbound = frappe.get_all(
			"Relay Message",
			filters={
				"thread": thread,
				"direction": "Incoming",
				"email_message_id": ["is", "set"],
			},
			fields=["email_message_id"],
			order_by="creation desc",
			limit=1,
		)
		return inbound[0].email_message_id if inbound else ""

	def _store_email_message_id(self, queue_doc, email_message_id: str):
		"""Persist the generated Message-ID on the linked Relay Message."""
		if not queue_doc.thread:
			return
		linked = frappe.get_all(
			"Relay Message",
			filters={
				"thread": queue_doc.thread,
				"direction": "Outgoing",
				"status": "Pending",
			},
			fields=["name"],
			order_by="creation desc",
			limit=1,
		)
		if linked:
			frappe.db.set_value("Relay Message", linked[0].name, "email_message_id", email_message_id)

	def _resolve_recipients(self, queue_doc) -> list[str]:
		"""Resolve email recipients from queue_doc data."""
		recipient_data = self._load_json(queue_doc.recipient_data)
		recipients = []

		for field in ("to", "cc", "bcc"):
			value = recipient_data.get(field)
			if value:
				if isinstance(value, str):
					recipients.extend([e.strip() for e in value.split(",") if e.strip()])
				elif isinstance(value, list):
					recipients.extend([e.strip() for e in value if e and isinstance(e, str)])

		if not recipients and queue_doc.contact:
			contact = frappe.get_doc("Relay Contact", queue_doc.contact)
			identifier = contact.get_identifier("Email")
			if identifier:
				recipients.append(identifier.identifier_value)

		return recipients

	def _load_json(self, value) -> dict:
		"""Safely load a JSON value."""
		if not value:
			return {}
		if isinstance(value, dict):
			return value
		try:
			import json

			return json.loads(value)
		except Exception:
			return {}

	def parse_inbound_webhook(
		self, payload: dict, account_name: str | None = None
	) -> InboundPayload:
		"""Frappe does not provide inbound email webhooks out of the box."""
		return InboundPayload(account_name=account_name or "")

	def map_status(self, provider_status: str) -> str:
		"""Map any non-error provider status to Sent."""
		if provider_status and provider_status.lower() in ("failed", "error", "bounced"):
			return "Failed"
		return "Sent"

	def supports(self, feature: str) -> bool:
		"""Email supports templates but not media, interactive, or status callbacks."""
		return feature in {"templates"}

	def normalize_identifier(self, identifier_type: str, value: str) -> str:
		"""Normalize an email address."""
		return super().normalize_identifier(identifier_type, value).lower()

	def get_default_recipient_type(self) -> str:
		return "Email"

	def format_template(
		self, template_doc, parameters: dict, recipient: NormalizedRecipient
	) -> dict:
		"""Build a generic email payload from a template."""
		body = template_doc.body_text or ""
		for key, value in (parameters or {}).items():
			body = body.replace(f"{{{key}}}", str(value))

		return {
			"subject": template_doc.template_name,
			"message": body,
			"recipients": [recipient.identifier_value],
		}


register("Email", EmailAdapter)
