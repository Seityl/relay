# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Relay Contact Identifier controller."""

import frappe
from frappe.model.document import Document


class RelayContactIdentifier(Document):
	"""A single identifier (phone, email, chat id, username) for a contact."""

	def validate(self):
		if not self.identifier_value:
			frappe.throw("Identifier value is required")

		self.identifier_value = self.identifier_value.strip()

		if self.identifier_type == "Phone":
			self.identifier_value = self.identifier_value.lstrip("+")

		if self.is_primary:
			self._clear_other_primary()

	def _clear_other_primary(self):
		"""Ensure only one primary identifier per type for a contact."""
		if not self.parent or not self.parenttype:
			return

		for row in frappe.get_doc(self.parenttype, self.parent).get("identifiers"):
			if row.name != self.name and row.identifier_type == self.identifier_type:
				row.is_primary = 0
