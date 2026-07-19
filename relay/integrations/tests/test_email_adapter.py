# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

import unittest
from unittest.mock import MagicMock, patch

from relay.integrations.base_adapter import NormalizedRecipient
from relay.integrations.email_adapter import EmailAdapter


class TestEmailAdapter(unittest.TestCase):
	def setUp(self):
		self.account = MagicMock()
		self.account.default_sender_address = "relay@example.com"
		self.adapter = EmailAdapter(self.account)

	def test_default_recipient_type(self):
		self.assertEqual(self.adapter.get_default_recipient_type(), "Email")

	def test_supports_templates_only(self):
		self.assertTrue(self.adapter.supports("templates"))
		self.assertFalse(self.adapter.supports("media"))
		self.assertFalse(self.adapter.supports("interactive"))
		self.assertFalse(self.adapter.supports("status_callbacks"))

	def test_normalize_identifier_lowercases_email(self):
		self.assertEqual(
			self.adapter.normalize_identifier("Email", " Test@Example.COM "),
			"test@example.com",
		)

	def test_map_status(self):
		self.assertEqual(self.adapter.map_status("queued"), "Sent")
		self.assertEqual(self.adapter.map_status("failed"), "Failed")
		self.assertEqual(self.adapter.map_status("error"), "Failed")

	def test_parse_inbound_webhook_returns_empty(self):
		result = self.adapter.parse_inbound_webhook({})
		self.assertEqual(result.messages, [])
		self.assertEqual(result.status_events, [])

	def test_format_template(self):
		template = MagicMock()
		template.body_text = "Hello {{name}}"
		template.template_name = "Greeting"
		result = self.adapter.format_template(
			template, {"name": "World"}, NormalizedRecipient("Email", "user@example.com")
		)
		self.assertEqual(result["subject"], "Greeting")
		self.assertEqual(result["recipients"], ["user@example.com"])

	@patch("relay.integrations.email_adapter.frappe.sendmail")
	@patch.object(EmailAdapter, "_generate_message_id")
	def test_send_resolves_recipients_from_recipient_data(self, mock_message_id, mock_sendmail):
		mock_message_id.return_value = "generated-id"
		queue_doc = MagicMock()
		queue_doc.recipient_data = {"to": "a@example.com, b@example.com", "cc": ["c@example.com"]}
		queue_doc.contact = None
		queue_doc.subject = "Test"
		queue_doc.html_body = "<p>Hi</p>"
		queue_doc.message_body = "Hi"
		queue_doc.reference_doctype = ""
		queue_doc.reference_name = ""
		queue_doc.thread = None

		message_id = self.adapter.send(queue_doc)

		self.assertEqual(message_id, "generated-id")
		mock_sendmail.assert_called_once()
		call_kwargs = mock_sendmail.call_args.kwargs
		self.assertEqual(call_kwargs["recipients"], ["a@example.com", "b@example.com", "c@example.com"])
		self.assertEqual(call_kwargs["subject"], "Test")
		self.assertEqual(call_kwargs["sender"], "relay@example.com")
