# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

import unittest

from relay.integrations.base_adapter import (
	Attachment,
	BaseChannelAdapter,
	InboundMessage,
	InboundPayload,
	NormalizedRecipient,
	StatusEvent,
	TemplateStatusEvent,
)


class ConcreteAdapter(BaseChannelAdapter):
	"""Minimal concrete adapter for testing."""

	def send(self, queue_doc) -> str:
		return "msg-id"

	def parse_inbound_webhook(self, payload, account_name=None):
		return InboundPayload()

	def map_status(self, provider_status: str) -> str:
		return provider_status.upper()


class TestBaseAdapter(unittest.TestCase):
	def test_default_behaviors(self):
		adapter = ConcreteAdapter(None)

		self.assertIsNone(adapter.verify_webhook({}))
		self.assertFalse(adapter.supports("anything"))
		self.assertEqual(adapter.normalize_identifier("Phone", " +123 "), "+123")
		self.assertEqual(adapter.validate_template(None), [])
		self.assertEqual(adapter.format_template(None, {}, NormalizedRecipient("Phone", "123")), {})
		self.assertEqual(adapter.get_default_recipient_type(), "Phone")

	def test_dataclasses(self):
		recipient = NormalizedRecipient(identifier_type="Email", identifier_value="a@b.com")
		attachment = Attachment(file_url="/x.pdf", file_name="x.pdf", mime_type="application/pdf")
		message = InboundMessage(
			provider_message_id="m1",
			from_identifier=recipient,
			attachments=[attachment],
		)
		status = StatusEvent(provider_message_id="m1", status="Sent")
		template = TemplateStatusEvent(provider_template_id="t1", status="Approved")
		payload = InboundPayload(
			messages=[message],
			status_events=[status],
			template_status_events=[template],
		)

		self.assertEqual(payload.messages[0].provider_message_id, "m1")
		self.assertEqual(payload.status_events[0].status, "Sent")
		self.assertEqual(payload.template_status_events[0].status, "Approved")
