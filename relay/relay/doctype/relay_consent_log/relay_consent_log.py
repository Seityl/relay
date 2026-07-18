# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Relay Consent Log controller."""

import frappe
from frappe.model.document import Document


class RelayConsentLog(Document):
	"""Audit record for a contact's opt-in or opt-out event."""

	def before_insert(self):
		if not self.timestamp:
			self.timestamp = frappe.utils.now()
