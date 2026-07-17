# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Relay Contact controller."""

import frappe
from frappe.model.document import Document


class RelayContact(Document):
	"""A contact that can receive or send messages."""

	def validate(self):
		# Backward compatibility: migrate legacy phone_number to identifiers
		if self.get("phone_number") and not any(
			i.identifier_type == "Phone" for i in self.get("identifiers", [])
		):
			self.append(
				"identifiers",
				{
					"identifier_type": "Phone",
					"identifier_value": self.phone_number.lstrip("+").strip(),
					"is_primary": 1,
				},
			)

		if not self.display_name:
			primary = self.get_primary_identifier()
			self.display_name = primary.identifier_value if primary else None

	def get_primary_identifier(self, identifier_type: str = "") -> dict | None:
		"""Return the primary identifier row of the given type, or any primary."""
		for row in self.get("identifiers", []):
			if row.is_primary and (not identifier_type or row.identifier_type == identifier_type):
				return row
		return None

	def get_identifier(self, identifier_type: str) -> dict | None:
		"""Return the first identifier row of the given type."""
		for row in self.get("identifiers", []):
			if row.identifier_type == identifier_type:
				return row
		return None

	@staticmethod
	def get_or_create(
		identifier_type: str,
		identifier_value: str,
		**kwargs,
	) -> "RelayContact":
		"""Fetch existing contact by identifier row or create a new one."""
		value = identifier_value.strip()
		if identifier_type == "Phone":
			value = value.lstrip("+")

		contact_name = frappe.db.get_value(
			"Relay Contact Identifier",
			{"identifier_type": identifier_type, "identifier_value": value},
			"parent",
		)
		if contact_name:
			return frappe.get_doc("Relay Contact", contact_name)

		doc = frappe.get_doc(
			{
				"doctype": "Relay Contact",
				"identifiers": [
					{
						"identifier_type": identifier_type,
						"identifier_value": value,
						"is_primary": 1,
					}
				],
				**kwargs,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc
