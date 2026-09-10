# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""WhatsApp through Twilio's Programmable Messaging API (#5).

Twilio differs from the Meta Cloud API in four ways that matter to anything
reading this file:

  1. **Requests and webhooks are form-encoded, not JSON.** Outbound is an
     ordinary `application/x-www-form-urlencoded` POST; inbound arrives as a
     flat dict of `MessageSid`, `From`, `To`, `Body`, `NumMedia`, ... rather
     than Meta's nested `entry[0].changes[0].value`.

  2. **Addresses carry a channel scheme.** Twilio's `From` is
     `whatsapp:+15550002222`. Relay stores identifiers bare -- normalization
     across the app is only `.strip().lstrip("+")` -- so that string would
     not match an identifier stored as `15550002222`, and `get_or_create`
     would silently make a second Contact. `normalize_identifier` below
     strips the scheme, and the inbound parser prefers Twilio's `WaId`,
     which is already bare digits.

  3. **The signature covers the URL, not just the body.** Meta signs
     HMAC-SHA256 over the raw body. Twilio signs HMAC-SHA1 over the full
     request URL concatenated with its POST parameters sorted by name, then
     base64. `BaseChannelAdapter.validate_webhook_signature` is handed only
     the body, so this implementation reaches `frappe.request` for the rest.

  4. **Inbound messages and delivery receipts share one endpoint.** Twilio
     distinguishes them by the presence of `MessageStatus`, so
     `parse_inbound_webhook` branches on that.

No Twilio SDK. The REST surface used here is one form POST with HTTP Basic
auth, and the signature is hmac + base64 from the standard library. Adding a
dependency to a public MIT app to save that is not a good trade.
"""

import base64
import hashlib
import hmac
import json

import frappe
import requests

from relay.integrations.base_adapter import (
	Attachment,
	BaseChannelAdapter,
	InboundMessage,
	InboundPayload,
	NormalizedRecipient,
	StatusEvent,
)
from relay.integrations.registry import register

API_ROOT = "https://api.twilio.com/2010-04-01"

#: Twilio message statuses -> Relay Message statuses.
#: Relay's options are Pending/Accepted/Sent/Delivered/Read/Failed/Cancelled
#: (relay_message.json:85). `undelivered` is a real delivery failure, not a
#: pending state, so it maps to Failed rather than being passed through.
STATUS_MAP = {
	"queued": "Accepted",
	"accepted": "Accepted",
	"scheduled": "Accepted",
	"sending": "Accepted",
	"sent": "Sent",
	"delivered": "Delivered",
	"read": "Read",
	"receiving": "Delivered",
	"received": "Delivered",
	"undelivered": "Failed",
	"failed": "Failed",
	"canceled": "Cancelled",
	"cancelled": "Cancelled",
}

#: Twilio media MIME prefix -> Relay `content_type` option
#: (relay_message.json:102).
MEDIA_KINDS = (
	("image/", "image"),
	("video/", "video"),
	("audio/", "audio"),
)


def _scheme(value: str) -> str:
	"""`15550002222` -> `whatsapp:+15550002222`."""
	bare = (value or "").strip()
	if bare.startswith("whatsapp:"):
		return bare
	return f"whatsapp:+{bare.lstrip('+')}"


class TwilioAdapter(BaseChannelAdapter):
	# --- credentials -----------------------------------------------------

	def _credentials(self) -> dict:
		"""Provider-specific config off the account's `credentials` JSON.

		The Account SID lives here rather than in a Password field because it
		travels in the request URL and in every webhook payload -- it is an
		identifier, not a secret. The Auth Token is the secret, and it uses
		`access_token`, which Frappe stores encrypted in `__Auth`.
		"""
		raw = self.account.credentials
		if not raw:
			return {}
		if isinstance(raw, dict):
			return raw
		try:
			return json.loads(raw)
		except (TypeError, ValueError):
			frappe.log_error(
				title="Relay Twilio Credentials Unreadable",
				message=f"Relay Account {self.account.name} has credentials that are not JSON",
			)
			return {}

	def _account_sid(self) -> str:
		sid = self._credentials().get("account_sid")
		if not sid:
			frappe.throw(
				f"Relay Account {self.account.name} has no Twilio account_sid. "
				"Set it in the account's Credentials JSON as "
				'{"account_sid": "AC..."}.'
			)
		return sid

	def _auth(self) -> tuple:
		token = self.account.get_access_token()
		if not token:
			frappe.throw(
				f"Relay Account {self.account.name} has no Auth Token. "
				"Set the Twilio Auth Token in the account's Access Token field."
			)
		return (self._account_sid(), token)

	def _sender(self) -> str:
		"""The WhatsApp sender, e.g. `whatsapp:+15550002222`."""
		if not self.account.identifier_value:
			frappe.throw(
				f"Relay Account {self.account.name} has no identifier_value, "
				"so there is no number to send from."
			)
		return _scheme(self.account.identifier_value)

	# --- outbound --------------------------------------------------------

	def send(self, queue_doc) -> str:
		payload = {
			"From": self._sender(),
			"To": _scheme(self._resolve_recipient(queue_doc)),
		}

		if queue_doc.message_type == "Template":
			payload.update(self._template_fields(queue_doc))
		else:
			body = queue_doc.message_body or ""
			if queue_doc.content_type and queue_doc.content_type != "text":
				if not queue_doc.media_url:
					frappe.throw(
						f"Queue entry {queue_doc.name} is {queue_doc.content_type} "
						"but carries no media_url"
					)
				payload["MediaUrl"] = queue_doc.media_url
				if body:
					payload["Body"] = body
			else:
				if not body:
					frappe.throw(f"Queue entry {queue_doc.name} has an empty message body")
				payload["Body"] = body

		# Twilio posts delivery receipts to this URL. Without it a message
		# reaches `Accepted` and never moves, because nothing tells relay it
		# was delivered or read.
		payload["StatusCallback"] = self._callback_url()

		response = self._post(f"Accounts/{self._account_sid()}/Messages.json", payload)
		sid = response.get("sid")
		if not sid:
			frappe.throw(f"Twilio accepted the message but returned no sid: {response}")
		return sid

	def _resolve_recipient(self, queue_doc) -> str:
		contact = frappe.get_doc("Relay Contact", queue_doc.contact)
		identifier = contact.get_identifier("Phone")
		if not identifier:
			frappe.throw(f"Relay Contact {contact.name} has no Phone identifier to send to")
		return identifier.identifier_value

	def _template_fields(self, queue_doc) -> dict:
		"""Twilio sends approved templates by Content SID, not by name."""
		if not queue_doc.template:
			frappe.throw(f"Queue entry {queue_doc.name} is a Template with no template set")

		template = frappe.get_doc("Relay Template", queue_doc.template)
		if not template.provider_template_id:
			frappe.throw(
				f"Relay Template {template.name} has no Provider Template ID. "
				"For Twilio this is the Content SID (HX...), created through "
				"the Content API and approved by Meta before it can be sent."
			)

		fields = {"ContentSid": template.provider_template_id}

		parameters = queue_doc.template_parameters
		if parameters:
			if isinstance(parameters, str):
				parameters = json.loads(parameters)
			# Twilio keys variables "1", "2", ... in a JSON string.
			fields["ContentVariables"] = json.dumps({str(k): str(v) for k, v in parameters.items()})

		return fields

	def _callback_url(self) -> str:
		"""Where Twilio should post delivery receipts.

		Carries `?account=` because that is the only provider-neutral
		discriminator the shared webhook handler has
		(`webhooks/handler.py:151-156`); without it a Twilio callback is
		attributed to whichever account happens to be the default incoming.
		"""
		return (
			f"{frappe.utils.get_url()}/api/method/relay.webhooks.handler.receive"
			f"?account={self.account.name}"
		)

	def _post(self, path: str, payload: dict) -> dict:
		url = f"{API_ROOT}/{path.lstrip('/')}"
		try:
			response = requests.post(url, data=payload, auth=self._auth(), timeout=30)
			response.raise_for_status()
		except requests.HTTPError:
			detail = response.text
			frappe.log_error(title="Relay Twilio API Error", message=f"{url}\n{detail}")
			# The queue catches this and applies its backoff.
			frappe.throw(f"Twilio rejected the request: {detail}")
		except requests.RequestException as e:
			frappe.log_error(title="Relay Twilio Transport Error", message=f"{url}\n{e}")
			frappe.throw(f"Could not reach Twilio: {e}")
		return response.json()

	# --- inbound ---------------------------------------------------------

	def parse_inbound_webhook(self, payload: dict, account_name: str | None = None) -> InboundPayload:
		normalized = InboundPayload(account_name=account_name or "")

		# Twilio uses one endpoint for both, distinguished by this key.
		if payload.get("MessageStatus") or payload.get("SmsStatus"):
			event = self._parse_status(payload)
			if event:
				normalized.status_events.append(event)
			return normalized

		message = self._parse_message(payload)
		if message:
			normalized.messages.append(message)
		return normalized

	def _parse_status(self, payload: dict) -> StatusEvent | None:
		sid = payload.get("MessageSid") or payload.get("SmsSid")
		if not sid:
			return None

		raw = payload.get("MessageStatus") or payload.get("SmsStatus") or ""
		error = {}
		if payload.get("ErrorCode"):
			error = {"code": payload.get("ErrorCode"), "message": payload.get("ErrorMessage", "")}

		return StatusEvent(
			provider_message_id=sid,
			# The handler branches on Relay statuses and never calls
			# map_status itself, so the mapping has to happen here.
			status=self.map_status(raw),
			error_payload=error,
			raw_payload=dict(payload),
		)

	def _parse_message(self, payload: dict) -> InboundMessage | None:
		sid = payload.get("MessageSid") or payload.get("SmsMessageSid")
		if not sid:
			return None

		attachments = self._parse_media(payload)
		content_type = "text"
		if attachments:
			content_type = attachments[0].relay_kind

		return InboundMessage(
			provider_message_id=sid,
			from_identifier=NormalizedRecipient(
				identifier_type="Phone",
				# `WaId` is bare digits; `From` is `whatsapp:+...`. Using
				# WaId keeps identifiers in the shape the rest of relay
				# stores them, so no duplicate Contact is created.
				identifier_value=self.normalize_identifier(
					"Phone", payload.get("WaId") or payload.get("From") or ""
				),
				display_name=payload.get("ProfileName") or "",
			),
			message_type="Freeform",
			content_type=content_type,
			body=payload.get("Body") or "",
			attachments=[a.attachment for a in attachments],
			raw_payload=dict(payload),
		)

	def _parse_media(self, payload: dict) -> list:
		"""Twilio hosts media behind the same Basic auth as the API."""
		try:
			count = int(payload.get("NumMedia") or 0)
		except (TypeError, ValueError):
			count = 0

		found = []
		for index in range(count):
			url = payload.get(f"MediaUrl{index}")
			if not url:
				continue
			mime = payload.get(f"MediaContentType{index}") or ""
			found.append(
				_Media(
					attachment=Attachment(
						file_url=url,
						file_name=f"{payload.get('MessageSid', 'media')}-{index}",
						mime_type=mime,
						content=self._download(url),
					),
					relay_kind=self._relay_kind(mime),
				)
			)
		return found

	def _relay_kind(self, mime: str) -> str:
		for prefix, kind in MEDIA_KINDS:
			if mime.startswith(prefix):
				return kind
		return "document"

	def _download(self, url: str) -> bytes:
		try:
			response = requests.get(url, auth=self._auth(), timeout=30)
			response.raise_for_status()
			return response.content
		except requests.RequestException as e:
			# A message with an unfetchable attachment is still worth
			# recording, so this is logged rather than raised.
			frappe.log_error(title="Relay Twilio Media Download Failed", message=f"{url}\n{e}")
			return b""

	# --- contract --------------------------------------------------------

	def map_status(self, provider_status: str) -> str:
		return STATUS_MAP.get((provider_status or "").lower(), provider_status)

	def normalize_identifier(self, identifier_type: str, value: str) -> str:
		bare = (value or "").strip()
		if bare.startswith("whatsapp:"):
			bare = bare[len("whatsapp:") :]
		return bare.lstrip("+")

	def supports(self, feature: str) -> bool:
		# `read_receipts` is deliberately absent. handler.py:299 calls
		# `adapter.mark_read(...)` on anything that claims it, and that
		# method is not on BaseChannelAdapter -- claiming the feature
		# without defining it raises AttributeError into a swallowed log.
		return feature in {"templates", "media", "webhooks", "status_callbacks"}

	def get_default_recipient_type(self) -> str:
		return "Phone"

	def signature_header(self) -> str:
		return "X-Twilio-Signature"

	def requires_valid_signature(self) -> bool:
		"""An unsigned request is refused whenever we could have checked one.

		The Auth Token is both the API credential and the signing key, so if
		sending works at all, verification is possible -- there is no
		configuration in which accepting an unsigned webhook is the right
		behaviour for this channel.
		"""
		return bool(self.account.get_access_token())

	def _signed_url(self, request) -> str:
		"""The URL Twilio signed, which is not always the one we received.

		Twilio signs the public `https://` URL it was configured with.
		gunicorn here listens on plain HTTP behind a TLS-terminating proxy,
		so `request.url` reports `http://` unless the WSGI environ has been
		fixed up -- and every signature would then fail to match for a reason
		that looks nothing like a proxy problem. `X-Forwarded-Proto` is what
		the proxy tells us the client actually used.
		"""
		url = request.url
		forwarded = request.headers.get("X-Forwarded-Proto", "")
		if forwarded and url.startswith("http://") and forwarded.lower() == "https":
			url = "https://" + url[len("http://") :]
		return url

	def validate_webhook_signature(self, payload_bytes: bytes, signature: str) -> bool:
		"""Twilio signs the URL plus the POST parameters sorted by name.

		`payload_bytes` alone cannot reproduce that, so the request is read
		from `frappe.request`. Note `request.form` rather than
		`frappe.local.form_dict`: Frappe merges query-string arguments into
		form_dict, and Twilio signed only its own POST body, so validating
		against the merged dict would fail as soon as the callback URL grew
		a `?account=` parameter -- which it always has.
		"""
		token = self.account.get_access_token()
		if not token or not signature:
			return False

		request = getattr(frappe, "request", None)
		if request is None:
			return False

		params = request.form.to_dict() if request.form else {}
		payload = self._signed_url(request) + "".join(f"{k}{params[k]}" for k in sorted(params))

		expected = base64.b64encode(
			hmac.new(token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
		).decode("utf-8")

		return hmac.compare_digest(expected, signature)


class _Media:
	"""Pairs a normalized Attachment with the Relay content_type it implies."""

	__slots__ = ("attachment", "relay_kind")

	def __init__(self, attachment: Attachment, relay_kind: str):
		self.attachment = attachment
		self.relay_kind = relay_kind


register("Twilio", TwilioAdapter)
