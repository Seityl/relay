# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Inbound webhook handler for messaging providers."""

import json
from typing import Any

import frappe
from werkzeug.wrappers import Response

from relay.relay.doctype.relay_account.relay_account import get_default_account
from relay.relay.doctype.relay_contact.relay_contact import RelayContact
from relay.relay.doctype.relay_thread.relay_thread import RelayThread
from relay.integrations.meta_cloud_api import MetaCloudAPIAdapter


@frappe.whitelist(allow_guest=True)
def receive():
	"""Public entry point for provider webhooks."""
	if frappe.request.method == "GET":
		return _verify_subscription()
	if frappe.request.method == "POST":
		return _handle_post()
	return Response("Method not allowed", status=405)


def _verify_subscription() -> Response:
	"""Handle provider subscription verification (Meta handshake)."""
	challenge = frappe.form_dict.get("hub.challenge")
	verify_token = frappe.form_dict.get("hub.verify_token")

	account = frappe.db.get_value(
		"Relay Account",
		{"webhook_verify_token": verify_token, "status": "Active"},
		"name",
	)
	if not account:
		frappe.throw("Invalid verify token")

	return Response(challenge, status=200)


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
		_process_payload(payload, _log)
		_log.status = "Processed"
		_log.processed_at = frappe.utils.now()
	except Exception:
		_log.status = "Failed"
		_log.error_log = frappe.get_traceback()
	finally:
		_log.save(ignore_permissions=True)
		frappe.db.commit()

	return "OK"


def _detect_event_type(payload: dict) -> str:
	"""Best-effort event type detection."""
	try:
		changes = payload.get("entry", [{}])[0].get("changes", [{}])[0]
		return changes.get("field", "unknown")
	except (KeyError, IndexError):
		return "unknown"


def _process_payload(payload: dict, log_doc):
	"""Route payload to the correct processor."""
	for entry in payload.get("entry", []):
		for change in entry.get("changes", []):
			field = change.get("field")
			value = change.get("value", {})

			if field == "messages":
				_process_messages(value, log_doc)
			elif field == "message_template_status_update":
				_process_template_status(value)


def _process_messages(value: dict, log_doc):
	"""Process inbound messages and status updates."""
	phone_id = value.get("metadata", {}).get("phone_number_id")
	account_name = _resolve_account(phone_id)
	log_doc.account = account_name

	contacts = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])}

	for message in value.get("messages", []):
		_process_inbound_message(message, account_name, contacts, phone_id)

	for status in value.get("statuses", []):
		_process_status_update(status, account_name)


def _resolve_account(phone_id: str | None) -> str | None:
	"""Resolve provider phone number id to a Relay Account."""
	if phone_id:
		account = frappe.db.get_value(
			"Relay Account", {"phone_number_id": phone_id, "status": "Active"}, "name"
		)
		if account:
			return account
	return get_default_account("incoming")


def _process_inbound_message(message: dict, account_name: str | None, contacts: dict, phone_id: str | None):
	"""Persist an inbound message and optionally mark it as read."""
	from_number = message.get("from", "")
	profile_name = contacts.get(from_number)

	contact = RelayContact.get_or_create(
		from_number,
		display_name=profile_name,
		profile_name=profile_name,
		account=account_name,
	)
	thread = RelayThread.get_or_create(contact.name, account_name or contact.account)

	message_type = message.get("type", "unknown")
	content_type = message_type
	body = ""
	media_payload = None
	interactive_payload = None

	if message_type == "text":
		body = message["text"].get("body", "")
	elif message_type == "reaction":
		body = message["reaction"].get("emoji", "")
		content_type = "reaction"
	elif message_type == "interactive":
		interactive_payload = message.get("interactive", {})
		content_type = "interactive"
		body = _summarize_interactive(interactive_payload)
	elif message_type == "order":
		body = "New order received"
		content_type = "order"
		media_payload = json.dumps(message.get("order", {}))
	elif message_type in ("image", "document", "audio", "video"):
		content_type = message_type
		body = message[message_type].get("caption", "")
		media_payload = message[message_type]
	elif message_type == "button":
		body = message["button"].get("text", "")
		content_type = "button"
	elif message_type == "location":
		body = json.dumps(message.get("location", {}))
		content_type = "location"
	else:
		body = json.dumps(message.get(message_type, {}))

	msg_doc = frappe.get_doc(
		{
			"doctype": "Relay Message",
			"thread": thread.name,
			"contact": contact.name,
			"account": account_name or contact.account,
			"direction": "Incoming",
			"status": "Delivered",
			"content_type": content_type,
			"message_body": body,
			"message_id": message.get("id"),
			"interactive_payload": json.dumps(interactive_payload) if interactive_payload else None,
		}
	)
	msg_doc.insert(ignore_permissions=True)

	if media_payload and content_type in ("image", "document", "audio", "video"):
		_download_and_attach_media(msg_doc, media_payload, account_name or contact.account)

	# Auto read receipt
	if account_name:
		account = frappe.get_doc("Relay Account", account_name)
		if account.allow_auto_read_receipt and phone_id:
			try:
				adapter = MetaCloudAPIAdapter(account)
				adapter.mark_read(message.get("id"))
			except Exception:
				frappe.log_error(title="Relay Auto Read Receipt Failed")

	frappe.db.commit()


def _process_status_update(status: dict, account_name: str | None):
	"""Update outbound message status from provider callbacks."""
	message_id = status.get("id")
	provider_status = status.get("status")
	if not message_id or not provider_status:
		return

	name = frappe.db.get_value("Relay Message", {"message_id": message_id}, "name")
	if not name:
		return

	doc = frappe.get_doc("Relay Message", name)
	if provider_status == "sent":
		doc.status = "Sent"
		doc.sent_at = frappe.utils.now()
	elif provider_status == "delivered":
		doc.status = "Delivered"
		doc.delivered_at = frappe.utils.now()
	elif provider_status == "read":
		doc.status = "Read"
		doc.read_at = frappe.utils.now()
	elif provider_status == "failed":
		doc.status = "Failed"
		doc.error_log = json.dumps(status.get("errors", {}))

	conversation = status.get("conversation", {})
	if conversation:
		doc.conversation_id = conversation.get("id")

	doc.save(ignore_permissions=True)
	frappe.db.commit()


def _process_template_status(value: dict):
	"""Update template approval status from provider callbacks."""
	template_id = value.get("message_template_id")
	status = value.get("event")
	if template_id and status:
		frappe.db.sql(
			"UPDATE `tabRelay Template` SET status = %(status)s WHERE provider_template_id = %(template_id)s",
			{"status": status, "template_id": template_id},
		)


def _summarize_interactive(payload: dict) -> str:
	"""Create a short text summary of an interactive response."""
	interactive_type = payload.get("type")
	if interactive_type == "button_reply":
		return payload.get("button_reply", {}).get("id", "")
	if interactive_type == "list_reply":
		return payload.get("list_reply", {}).get("id", "")
	if interactive_type == "nfm_reply":
		response = json.loads(payload.get("nfm_reply", {}).get("response_json", "{}"))
		return ", ".join(f"{k}: {v}" for k, v in response.items() if v)
	return json.dumps(payload)


def _download_and_attach_media(msg_doc, media_payload: dict, account_name: str):
	"""Download incoming media and attach it to the message."""
	account = frappe.get_doc("Relay Account", account_name)
	adapter = MetaCloudAPIAdapter(account)
	media_id = media_payload.get("id")
	if not media_id:
		return

	try:
		content, mime_type = adapter.download_media(media_id)
		extension = mime_type.split("/")[-1]
		file_name = f"{frappe.generate_hash(length=10)}.{extension}"

		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"attached_to_doctype": "Relay Message",
				"attached_to_name": msg_doc.name,
				"content": content,
			}
		).save(ignore_permissions=True)

		msg_doc.media = file_doc.file_url
		msg_doc.media_mime_type = mime_type
		msg_doc.save(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "Relay Media",
				"message": msg_doc.name,
				"contact": msg_doc.contact,
				"media_type": msg_doc.content_type,
				"provider_media_id": media_id,
				"mime_type": mime_type,
				"file": file_doc.file_url,
				"caption": msg_doc.message_body,
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Relay Media Download Failed")
