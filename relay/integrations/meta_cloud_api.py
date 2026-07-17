# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Adapter for Meta's Cloud messaging API."""

import json
from typing import Any

import frappe
import requests
from frappe import _
from werkzeug.wrappers import Response

from relay.integrations.base_adapter import (
	Attachment,
	BaseChannelAdapter,
	InboundMessage,
	InboundPayload,
	NormalizedRecipient,
	StatusEvent,
	TemplateStatusEvent,
)
from relay.integrations.registry import register


class MetaCloudAPIAdapter(BaseChannelAdapter):
	"""Send and receive messages via Meta's Cloud API."""

	def __init__(self, account_doc):
		super().__init__(account_doc)
		self.token = account_doc.get_access_token()
		self.base_url = account_doc.get_api_base_url()
		self.phone_id = account_doc.phone_number_id

	def _headers(self) -> dict:
		return {
			"Authorization": f"Bearer {self.token}",
			"Content-Type": "application/json",
		}

	def _url(self, path: str) -> str:
		return f"{self.base_url}/{path.lstrip('/')}"

	def send(self, queue_doc) -> str:
		"""Dispatch a queue entry and return the provider message id."""
		phone = self._resolve_recipient(queue_doc.contact)

		if queue_doc.message_type == "Template":
			payload = self._build_template_payload(phone, queue_doc)
		elif queue_doc.message_type == "Interactive":
			payload = self._build_interactive_payload(phone, queue_doc)
		else:
			payload = self._build_freeform_payload(phone, queue_doc)

		response = self._post(f"{self.phone_id}/messages", payload)
		return response["messages"][0]["id"]

	def _resolve_recipient(self, contact_name: str) -> str:
		"""Return normalized phone number for a Relay Contact."""
		contact = frappe.get_doc("Relay Contact", contact_name)
		identifier = contact.get_identifier("Phone")
		value = identifier.identifier_value if identifier else contact_name
		return self.normalize_identifier("Phone", value)

	def _build_freeform_payload(self, phone: str, queue_doc) -> dict:
		content_type = queue_doc.content_type or "text"
		data: dict[str, Any] = {
			"messaging_product": "whatsapp",
			"recipient_type": "individual",
			"to": phone,
			"type": content_type,
		}

		if content_type == "text":
			data["text"] = {"preview_url": True, "body": queue_doc.message_body or ""}
		elif content_type in ("image", "document", "audio", "video"):
			data[content_type] = {"link": queue_doc.media_url}
			if queue_doc.message_body:
				data[content_type]["caption"] = queue_doc.message_body
		else:
			frappe.throw(_("Unsupported freeform content type: {0}").format(content_type))

		return data

	def _build_template_payload(self, phone: str, queue_doc) -> dict:
		if not queue_doc.template:
			frappe.throw(_("Template is required for template messages"))

		template_doc = frappe.get_doc("Relay Template", queue_doc.template)
		params = json.loads(queue_doc.template_parameters or "{}")

		parameters = [
			{"type": "text", "text": str(params.get(str(idx + 1), params.get(p.parameter_name, "")))}
			for idx, p in enumerate(sorted(template_doc.parameters, key=lambda x: x.parameter_index))
		]

		components = [{"type": "body", "parameters": parameters}]

		if template_doc.header_type and template_doc.header_sample:
			header_param = self._build_header_parameter(template_doc)
			if header_param:
				components.append({"type": "header", "parameters": [header_param]})

		language_code = template_doc.language_code or "en"
		return {
			"messaging_product": "whatsapp",
			"recipient_type": "individual",
			"to": phone,
			"type": "template",
			"template": {
				"name": template_doc.actual_name or template_doc.template_name,
				"language": {"code": language_code},
				"components": components,
			},
		}

	def _build_header_parameter(self, template_doc) -> dict | None:
		"""Build a header parameter for media templates."""
		if template_doc.header_type == "IMAGE":
			return {"type": "image", "image": {"link": template_doc.header_sample}}
		if template_doc.header_type == "DOCUMENT":
			return {
				"type": "document",
				"document": {"link": template_doc.header_sample, "filename": "document.pdf"},
			}
		if template_doc.header_type == "VIDEO":
			return {"type": "video", "video": {"link": template_doc.header_sample}}
		if template_doc.header_type == "TEXT":
			return {"type": "text", "text": template_doc.header_sample}
		return None

	def _build_interactive_payload(self, phone: str, queue_doc) -> dict:
		payload = json.loads(queue_doc.interactive_payload or "{}")
		return {
			"messaging_product": "whatsapp",
			"recipient_type": "individual",
			"to": phone,
			"type": "interactive",
			"interactive": payload,
		}

	def _post(self, path: str, payload: dict) -> dict:
		url = self._url(path)
		response = requests.post(url, headers=self._headers(), json=payload, timeout=60)
		try:
			response.raise_for_status()
		except requests.HTTPError as e:
			frappe.log_error(title="Relay Meta API Error", message=response.text)
			frappe.throw(_("Failed to send message: {0}").format(response.text))
		return response.json()

	def mark_read(self, message_id: str):
		"""Mark a delivered message as read on the provider side."""
		return self._post(
			f"{self.phone_id}/messages",
			{"messaging_product": "whatsapp", "status": "read", "message_id": message_id},
		)

	def download_media(self, media_id: str) -> tuple[bytes, str]:
		"""Download media bytes and MIME type from the provider."""
		meta_url = self._url(f"{media_id}/")
		response = requests.get(meta_url, headers={"Authorization": f"Bearer {self.token}"}, timeout=60)
		response.raise_for_status()
		meta = response.json()

		media_url = meta.get("url")
		mime_type = meta.get("mime_type", "application/octet-stream")
		if not media_url:
			frappe.throw(_("Media URL not found in provider response"))

		content_response = requests.get(
			media_url, headers={"Authorization": f"Bearer {self.token}"}, timeout=120
		)
		content_response.raise_for_status()
		return content_response.content, mime_type

	def verify_webhook(self, request_args: dict) -> Response | None:
		"""Handle Meta subscription verification handshake."""
		challenge = request_args.get("hub.challenge")
		verify_token = request_args.get("hub.verify_token")

		account = frappe.db.get_value(
			"Relay Account",
			{"webhook_verify_token": verify_token, "status": "Active"},
			"name",
		)
		if not account:
			frappe.throw("Invalid verify token")

		return Response(challenge, status=200)

	def parse_inbound_webhook(
		self, payload: dict, account_name: str | None = None
	) -> InboundPayload:
		"""Normalize a Meta webhook payload into Relay dataclasses."""
		result = InboundPayload(account_name=account_name or "")

		for entry in payload.get("entry", []):
			for change in entry.get("changes", []):
				field = change.get("field")
				value = change.get("value", {})

				if field == "messages":
					self._parse_messages(value, result)
				elif field == "message_template_status_update":
					self._parse_template_status(value, result)

		return result

	def _parse_messages(self, value: dict, result: InboundPayload):
		"""Parse inbound messages and status updates from a Meta value object."""
		phone_id = value.get("metadata", {}).get("phone_number_id")
		account_name = result.account_name or self._resolve_account(phone_id)
		result.account_name = account_name

		contacts = {
			c.get("wa_id"): c.get("profile", {}).get("name")
			for c in value.get("contacts", [])
		}

		for message in value.get("messages", []):
			result.messages.append(self._parse_inbound_message(message, contacts))

		for status in value.get("statuses", []):
			result.status_events.append(self._parse_status_update(status))

	def _resolve_account(self, phone_id: str | None) -> str:
		"""Resolve provider phone number id to a Relay Account."""
		from relay.relay.doctype.relay_account.relay_account import get_default_account

		if phone_id:
			account = frappe.db.get_value(
				"Relay Account", {"phone_number_id": phone_id, "status": "Active"}, "name"
			)
			if account:
				return account
		return get_default_account("incoming") or ""

	def _parse_inbound_message(
		self, message: dict, contacts: dict
	) -> InboundMessage:
		"""Normalize a single Meta inbound message."""
		from_number = message.get("from", "")
		profile_name = contacts.get(from_number)

		message_type = message.get("type", "unknown")
		content_type = message_type
		body = ""
		attachments = []
		interactive_payload = None

		if message_type == "text":
			body = message["text"].get("body", "")
		elif message_type == "reaction":
			body = message["reaction"].get("emoji", "")
			content_type = "reaction"
		elif message_type == "interactive":
			interactive_payload = message.get("interactive", {})
			content_type = "interactive"
			body = self._summarize_interactive(interactive_payload)
		elif message_type == "order":
			body = "New order received"
			content_type = "order"
		elif message_type in ("image", "document", "audio", "video"):
			content_type = message_type
			body = message[message_type].get("caption", "")
			media_payload = message[message_type]
			attachments.append(
				self._build_attachment(media_payload, fallback_body=body)
			)
		elif message_type == "button":
			body = message["button"].get("text", "")
			content_type = "button"
		elif message_type == "location":
			body = json.dumps(message.get("location", {}))
			content_type = "location"
		else:
			body = json.dumps(message.get(message_type, {}))

		return InboundMessage(
			provider_message_id=message.get("id", ""),
			conversation_id=message.get("context", {}).get("id", ""),
			from_identifier=NormalizedRecipient(
				identifier_type="Phone",
				identifier_value=self.normalize_identifier("Phone", from_number),
				display_name=profile_name or "",
			),
			message_type="Freeform",
			content_type=content_type,
			body=body,
			attachments=attachments,
			interactive_payload=interactive_payload or {},
			reply_to_message_id=message.get("context", {}).get("id", ""),
			is_reply=bool(message.get("context", {}).get("id")),
			raw_payload=message,
		)

	def _parse_status_update(self, status: dict) -> StatusEvent:
		"""Normalize a Meta status update."""
		return StatusEvent(
			provider_message_id=status.get("id", ""),
			status=self.map_status(status.get("status", "")),
			conversation_id=status.get("conversation", {}).get("id", ""),
			error_payload=status.get("errors", {}),
			raw_payload=status,
		)

	def _parse_template_status(self, value: dict, result: InboundPayload):
		"""Normalize a Meta template status update."""
		result.template_status_events.append(
			TemplateStatusEvent(
				provider_template_id=value.get("message_template_id", ""),
				status=value.get("event", ""),
				raw_payload=value,
			)
		)

	def map_status(self, provider_status: str) -> str:
		"""Map Meta status strings to Relay Message statuses."""
		mapping = {
			"sent": "Sent",
			"delivered": "Delivered",
			"read": "Read",
			"failed": "Failed",
			"deleted": "Failed",
		}
		return mapping.get(provider_status, provider_status)

	def supports(self, feature: str) -> bool:
		"""Meta Cloud API supports templates, media, interactive, and read receipts."""
		return feature in {
			"templates",
			"media",
			"interactive",
			"read_receipts",
			"webhooks",
			"status_callbacks",
		}

	def normalize_identifier(self, identifier_type: str, value: str) -> str:
		"""Strip leading + from phone numbers for Meta."""
		value = super().normalize_identifier(identifier_type, value)
		if identifier_type == "Phone":
			return value.lstrip("+")
		return value

	def validate_template(self, template_doc) -> list[str]:
		"""Validate a template for Meta Cloud API."""
		errors = []
		if not template_doc.language_code:
			errors.append("Language code is required for Meta templates")
		if not template_doc.category:
			errors.append("Category is required for Meta templates")
		return errors

	def format_template(
		self, template_doc, parameters: dict, recipient: NormalizedRecipient
	) -> dict:
		"""Build a Meta-specific template payload."""
		params = [
			{"type": "text", "text": str(parameters.get(str(idx + 1), parameters.get(p.parameter_name, "")))}
			for idx, p in enumerate(sorted(template_doc.parameters, key=lambda x: x.parameter_index))
		]

		components = [{"type": "body", "parameters": params}]
		if template_doc.header_type and template_doc.header_sample:
			header_param = self._build_header_parameter(template_doc)
			if header_param:
				components.append({"type": "header", "parameters": [header_param]})

		language_code = template_doc.language_code or "en"
		return {
			"messaging_product": "whatsapp",
			"recipient_type": "individual",
			"to": self.normalize_identifier("Phone", recipient.identifier_value),
			"type": "template",
			"template": {
				"name": template_doc.actual_name or template_doc.template_name,
				"language": {"code": language_code},
				"components": components,
			},
		}

	def get_default_recipient_type(self) -> str:
		return "Phone"

	def _build_attachment(self, media_payload: dict, fallback_body: str = "") -> Attachment:
		"""Build an Attachment, downloading media bytes when possible."""
		media_id = media_payload.get("id", "")
		mime_type = media_payload.get("mime_type", "")
		content = b""

		if media_id:
			try:
				content, mime_type = self.download_media(media_id)
			except Exception:
				frappe.log_error(title="Relay Meta Media Download Failed")

		return Attachment(
			file_url="",
			file_name=media_payload.get("filename") or fallback_body or media_id,
			mime_type=mime_type,
			content=content,
		)

	def _summarize_interactive(self, payload: dict) -> str:
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


register("Meta Cloud API", MetaCloudAPIAdapter)
