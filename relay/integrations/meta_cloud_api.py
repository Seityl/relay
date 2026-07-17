"""Adapter for Meta's Cloud messaging API.

This module is intentionally provider-specific. A new adapter can be added for
SMS, RCS, or other messaging providers by implementing the same interface.
"""

import json
from typing import Any

import frappe
import requests
from frappe import _


class MetaCloudAPIAdapter:
	"""Send and receive messages via Meta's Cloud API."""

	def __init__(self, account_doc):
		self.account = account_doc
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
		phone = self._format_number(queue_doc.contact)

		if queue_doc.message_type == "Template":
			payload = self._build_template_payload(phone, queue_doc)
		elif queue_doc.message_type == "Interactive":
			payload = self._build_interactive_payload(phone, queue_doc)
		else:
			payload = self._build_freeform_payload(phone, queue_doc)

		response = self._post(f"{self.phone_id}/messages", payload)
		return response["messages"][0]["id"]

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

		return {
			"messaging_product": "whatsapp",
			"recipient_type": "individual",
			"to": phone,
			"type": "template",
			"template": {
				"name": template_doc.actual_name or template_doc.template_name,
				"language": {"code": template_doc.language_code},
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

	def _format_number(self, contact_name: str) -> str:
		phone = frappe.db.get_value("Relay Contact", contact_name, "phone_number")
		return phone.lstrip("+") if phone else contact_name.lstrip("+")
