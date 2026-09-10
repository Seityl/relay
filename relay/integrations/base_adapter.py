# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Abstract base class and dataclasses for Relay channel adapters.

A channel adapter translates between Relay's generic messaging model and a
specific provider (WhatsApp/Meta, Email, SMS, Telegram, etc.). New adapters are
registered in relay.integrations.registry and referenced by Relay Channel records.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from werkzeug.wrappers import Response


@dataclass
class NormalizedRecipient:
	"""A normalized recipient address for any channel."""

	identifier_type: str  # e.g. Phone, Email, Chat ID
	identifier_value: str
	display_name: str = ""
	channel: str = ""  # optional channel link


@dataclass
class Attachment:
	"""Generic attachment metadata."""

	file_url: str = ""
	file_name: str = ""
	mime_type: str = ""
	content: bytes = b""


@dataclass
class InboundMessage:
	"""Provider-agnostic inbound message."""

	provider_message_id: str = ""
	conversation_id: str = ""
	from_identifier: NormalizedRecipient = field(default_factory=NormalizedRecipient)
	message_type: str = "Freeform"  # Freeform, Template, Interactive, Notification
	content_type: str = "text"  # text, html, image, document, audio, video, location, etc.
	body: str = ""
	subject: str = ""
	html_body: str = ""
	recipient_data: dict = field(default_factory=dict)
	attachments: list[Attachment] = field(default_factory=list)
	interactive_payload: dict = field(default_factory=dict)
	reply_to_message_id: str = ""
	is_reply: bool = False
	raw_payload: dict = field(default_factory=dict)


@dataclass
class StatusEvent:
	"""Provider-agnostic status update for an outbound message."""

	provider_message_id: str = ""
	status: str = ""  # mapped to Relay status by adapter
	conversation_id: str = ""
	error_payload: dict = field(default_factory=dict)
	raw_payload: dict = field(default_factory=dict)


@dataclass
class TemplateStatusEvent:
	"""Provider-agnostic template approval status event."""

	provider_template_id: str = ""
	status: str = ""
	raw_payload: dict = field(default_factory=dict)


@dataclass
class InboundPayload:
	"""Normalized result of parsing a provider webhook payload."""

	messages: list[InboundMessage] = field(default_factory=list)
	status_events: list[StatusEvent] = field(default_factory=list)
	template_status_events: list[TemplateStatusEvent] = field(default_factory=list)
	account_name: str = ""  # resolved Relay Account


class BaseChannelAdapter(ABC):
	"""Every messaging provider implements this interface."""

	def __init__(self, account_doc):
		self.account = account_doc

	@abstractmethod
	def send(self, queue_doc) -> str:
		"""Dispatch a queue entry and return the provider message id."""

	@abstractmethod
	def parse_inbound_webhook(
		self, payload: dict, account_name: str | None = None
	) -> InboundPayload:
		"""Normalize a provider webhook payload into Relay dataclasses."""

	def verify_webhook(self, request_args: dict) -> Response | None:
		"""Optional provider handshake. Return None if not supported."""
		return None

	def signature_header(self) -> str:
		"""The request header carrying this provider's signature.

		The webhook handler is shared by every provider, so it cannot know
		the header name -- Meta sends `X-Hub-Signature-256`, Twilio sends
		`X-Twilio-Signature`. It was hardcoded to Meta's, which meant any
		other provider's signature was never read and, because the check is
		skipped when no signature is found, never verified either.
		"""
		return "X-Hub-Signature-256"

	def requires_valid_signature(self) -> bool:
		"""Whether an unsigned request must be rejected.

		The handler only validates when a signature is present, so returning
		False means an attacker can skip verification by omitting the header
		on a guest-callable endpoint. The default is False because that is
		the behaviour the Meta adapter was built and tested against -- it
		returns True from `validate_webhook_signature` when no app secret is
		configured, and `test_missing_secret_allows_through` pins that. An
		adapter that can always verify should return True and close the hole
		for its own channel.
		"""
		return False

	def validate_webhook_signature(self, payload_bytes: bytes, signature: str) -> bool:
		"""Validate an incoming webhook signature. Return True if valid."""
		return False

	@abstractmethod
	def map_status(self, provider_status: str) -> str:
		"""Map provider status string to a Relay Message status."""

	def supports(self, feature: str) -> bool:
		"""Return whether this adapter supports a capability.

		Features: templates, media, interactive, read_receipts, webhooks, status_callbacks
		"""
		return False

	def normalize_identifier(self, identifier_type: str, value: str) -> str:
		"""Normalize an identifier value for this provider."""
		return value.strip()

	def validate_template(self, template_doc) -> list[str]:
		"""Return a list of validation errors for a template. Empty if valid."""
		return []

	def format_template(
		self, template_doc, parameters: dict, recipient: NormalizedRecipient
	) -> dict:
		"""Build provider-specific payload for a template message."""
		return {}

	def get_default_recipient_type(self) -> str:
		"""Return the default identifier type for this channel."""
		return "Phone"
