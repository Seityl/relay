# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Relay Contact controller."""

import frappe
from frappe.model.document import Document


class RelayContact(Document):
	"""A contact that can receive or send messages."""

	def autoname(self):
		self.name = self.phone_number.lstrip("+")

	def validate(self):
		self.phone_number = self.phone_number.lstrip("+").strip()

	@staticmethod
	def get_or_create(phone_number: str, **kwargs) -> "RelayContact":
		"""Fetch existing contact or create a new one."""
		phone = phone_number.lstrip("+").strip()
		if frappe.db.exists("Relay Contact", phone):
			return frappe.get_doc("Relay Contact", phone)

		doc = frappe.get_doc({"doctype": "Relay Contact", "phone_number": phone, **kwargs})
		doc.insert(ignore_permissions=True)
		return doc
