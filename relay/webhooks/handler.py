# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Inbound webhook handler for messaging providers."""

import json
from typing import Any

import frappe
from werkzeug.wrappers import Response

from relay.integrations.base_adapter import (
	InboundMessage,
	StatusEvent,
	TemplateStatusEvent,
)
from relay.integrations.registry import get_adapter
from relay.relay.doctype.relay_account.relay_account import get_default_account
from relay.relay.doctype.relay_contact.relay_contact import RelayContact
from relay.relay.doctype.relay_thread.relay_thread import RelayThread


@frappe.whitelist(allow_guest=True)
def receive():
	"""Public entry point for provider webhooks."""
	if frappe.request.method == "GET":
		return _verify_subscription()
	if frappe.request.method == "POST":
		return _handle_post()
	return Response("Method not allowed", status=405)


def _verify_subscription() -> Response:
	"""Handle provider subscription verification."""
	request_args = frappe.request.args.to_dict()
	account_name = _resolve_account_from_args(request_args)
	if not account_name:
		return Response("Invalid verify token", status=403)

	account = frappe.get_doc("Relay Account", account_name)
	channel = frappe.get_doc("Relay Channel", account.channel)
	adapter = get_adapter(channel.provider, account)

	result = adapter.verify_webhook(request_args)
	if result is None:
		return Response("Verification not supported", status=400)
	return result


def _handle_post() -> str:
	"""Handle inbound webhook payload."""
	payload = frappe.local.form_dict
	if not payload:
		try:
			payload = json.loads(frappe.request.data or "{}")
		except json.JSONDecodeError:
			return Response("Invalid JSON", status=400)

	_log = frappe.get_doc(
		{
			"doctype": "Relay Webhook Log",
			"event_type": _detect_event_type(payload),
			"payload": json.dumps(payload),
			"status": "Received",
		}
	)
	_log.insert(ignore_permissions=True)

	try:
		account_name = _resolve_account_from_payload(payload)
		_log.account = account_name

		if account_name:
			account = frappe.get_doc("Relay Account", account_name)
			channel = frappe.get_doc("Relay Channel", account.channel)
			adapter = get_adapter(channel.provider, account)
			normalized = adapter.parse_inbound_webhook(payload, account_name=account_name)
		else:
			# Fallback for unresolvable payloads
			normalized = _empty_payload()

		_process_normalized_payload(normalized, account_name)
		_log.status = "Processed"
		_log.processed_at = frappe.utils.now()
	except Exception:
		_log.status = "Failed"
		_log.error_log = frappe.get_traceback()
	finally:
		_log.save(ignore_permissions=True)
		frappe.db.commit()

	return "OK"


def _resolve_account_from_args(request_args: dict) -> str | None:
	"""Resolve account from GET request arguments."""
	# Explicit account override
	if request_args.get("account"):
		account = frappe.db.get_value(
			"Relay Account", {"name": request_args["account"], "status": "Active"}, "name"
		)
		if account:
			return account

	# Meta-style verify token
	verify_token = request_args.get("hub.verify_token")
	if verify_token:
		account = frappe.db.get_value(
			"Relay Account",
			{"webhook_verify_token": verify_token, "status": "Active"},
			"name",
		)
		if account:
			return account

	return None


def _resolve_account_from_payload(payload: dict) -> str | None:
	"""Resolve account from POST payload."""
	# Explicit query override
	request_args = frappe.request.args.to_dict()
	if request_args.get("account"):
		account = frappe.db.get_value(
			"Relay Account", {"name": request_args["account"], "status": "Active"}, "name"
		)
		if account:
			return account

	# Meta-style phone number id resolution
	try:
		phone_id = (
			payload.get("entry", [{}])[0]
			.get("changes", [{}])[0]
			.get("value", {})
			.get("metadata", {})
			.get("phone_number_id")
		)
		if phone_id:
			account = frappe.db.get_value(
				"Relay Account", {"phone_number_id": phone_id, "status": "Active"}, "name"
			)
			if account:
				return account
	except (KeyError, IndexError):
		pass

	return get_default_account("incoming")


def _detect_event_type(payload: dict) -> str:
	"""Best-effort event type detection."""
	try:
		changes = payload.get("entry", [{}])[0].get("changes", [{}])[0]
		return changes.get("field", "unknown")
	except (KeyError, IndexError):
		return "unknown"


def _empty_payload():
	"""Return an empty normalized payload."""
	from relay.integrations.base_adapter import InboundPayload

	return InboundPayload()


def _process_normalized_payload(normalized, account_name: str | None):
	"""Persist normalized inbound events."""
	account_name = account_name or normalized.account_name or ""

	for message in normalized.messages:
		_persist_inbound_message(message, account_name)

	for status_event in normalized.status_events:
		_persist_status_event(status_event)

	for template_event in normalized.template_status_events:
		_persist_template_status_event(template_event)


def _persist_inbound_message(message: InboundMessage, account_name: str):
	"""Persist a normalized inbound message."""
	contact = RelayContact.get_or_create(
		message.from_identifier.identifier_type,
		message.from_identifier.identifier_value,
		display_name=message.from_identifier.display_name,
		profile_name=message.from_identifier.display_name,
		account=account_name,
	)
	thread = RelayThread.get_or_create(contact.name, account_name or contact.account)

	msg_doc = frappe.get_doc(
		{
			"doctype": "Relay Message",
			"thread": thread.name,
			"contact": contact.name,
			"account": account_name or contact.account,
			"direction": "Incoming",
			"status": "Delivered",
			"message_type": message.message_type,
			"content_type": message.content_type,
			"subject": message.subject,
			"message_body": message.body,
			"html_body": message.html_body,
			"message_id": message.provider_message_id,
			"conversation_id": message.conversation_id,
			"interactive_payload": (
				json.dumps(message.interactive_payload) if message.interactive_payload else None
			),
			"recipient_data": json.dumps(message.recipient_data) if message.recipient_data else None,
		}
	)
	msg_doc.insert(ignore_permissions=True)

	for attachment in message.attachments:
		file_doc = None
		if attachment.content:
			file_name = attachment.file_name or f"{frappe.generate_hash(length=10)}.bin"
			file_doc = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": file_name,
					"attached_to_doctype": "Relay Message",
					"attached_to_name": msg_doc.name,
					"content": attachment.content,
				}
			).save(ignore_permissions=True)

		msg_doc.append(
			"attachments",
			{
				"file_url": file_doc.file_url if file_doc else attachment.file_url,
				"file_name": (attachment.file_name or file_doc.file_name) if file_doc else attachment.file_name,
				"mime_type": attachment.mime_type,
				"caption": "",
			},
		)

	if message.attachments:
		msg_doc.save(ignore_permissions=True)

	# Auto read receipt
	if account_name:
		account = frappe.get_doc("Relay Account", account_name)
		channel = frappe.get_doc("Relay Channel", account.channel)
		adapter = get_adapter(channel.provider, account)
		if account.allow_auto_read_receipt and adapter.supports("read_receipts"):
			try:
				adapter.mark_read(message.provider_message_id)
			except Exception:
				frappe.log_error(title="Relay Auto Read Receipt Failed")

	frappe.db.commit()


def _persist_status_event(event: StatusEvent):
	"""Update outbound message status from a normalized status event."""
	if not event.provider_message_id:
		return

	name = frappe.db.get_value("Relay Message", {"message_id": event.provider_message_id}, "name")
	if not name:
		return

	doc = frappe.get_doc("Relay Message", name)
	doc.status = event.status

	if event.status == "Sent":
		doc.sent_at = doc.sent_at or frappe.utils.now()
	elif event.status == "Delivered":
		doc.delivered_at = doc.delivered_at or frappe.utils.now()
	elif event.status == "Read":
		doc.read_at = doc.read_at or frappe.utils.now()
	elif event.status == "Failed":
		doc.error_log = json.dumps(event.error_payload) if event.error_payload else ""

	if event.conversation_id:
		doc.conversation_id = event.conversation_id

	doc.save(ignore_permissions=True)
	frappe.db.commit()


def _persist_template_status_event(event: TemplateStatusEvent):
	"""Update template approval status from a normalized template status event."""
	if not event.provider_template_id or not event.status:
		return

	frappe.db.sql(
		"UPDATE `tabRelay Template` SET status = %(status)s WHERE provider_template_id = %(template_id)s",
		{"status": event.status, "template_id": event.provider_template_id},
	)
