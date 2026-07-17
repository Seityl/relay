# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Relay Channel controller."""

import importlib

import frappe
from frappe.model.document import Document


class RelayChannel(Document):
	"""A messaging channel configuration."""

	def get_adapter_class(self):
		"""Resolve and return the adapter class for this channel."""
		path = self.adapter_class or self.handler_module
		if not path:
			frappe.throw("No adapter class configured for this channel")

		module_path, class_name = path.rsplit(".", 1)
		module = importlib.import_module(module_path)
		return getattr(module, class_name)
