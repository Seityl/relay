# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

import unittest

from relay.integrations.base_adapter import BaseChannelAdapter, InboundPayload
from relay.integrations.registry import (
	get_adapter,
	get_registered_adapters,
	register,
)


class DummyAdapter(BaseChannelAdapter):
	def send(self, queue_doc) -> str:
		return "dummy"

	def parse_inbound_webhook(self, payload, account_name=None):
		return InboundPayload()

	def map_status(self, provider_status: str) -> str:
		return provider_status


class TestRegistry(unittest.TestCase):
	def test_register_and_get(self):
		register("DummyProvider", DummyAdapter)
		adapter = get_adapter("DummyProvider", None)
		self.assertIsInstance(adapter, DummyAdapter)

	def test_registered_adapters_includes_builtins(self):
		adapters = get_registered_adapters()
		self.assertIn("Meta Cloud API", adapters)
		self.assertIn("Email", adapters)
