# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Relay Auto Reply Rule controller."""

import frappe
from frappe.model.document import Document


class RelayAutoReplyRule(Document):
	"""A rule that matches inbound messages and triggers automated responses/actions."""

	def validate(self):
		if self.match_type == "Keyword" and not self.keywords:
			frappe.throw("Keywords are required for Keyword match type")
		if self.match_type == "Regex" and not self.regex_pattern:
			frappe.throw("Regex Pattern is required for Regex match type")
		if self.match_type == "Intent" and not self.intent:
			frappe.throw("Intent is required for Intent match type")
		if self.response_type == "Template" and not self.template:
			frappe.throw("Template is required when Response Type is Template")
		if self.response_type == "Freeform" and not self.response_body:
			frappe.throw("Response Body is required when Response Type is Freeform")
