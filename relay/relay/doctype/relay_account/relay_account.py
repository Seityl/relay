"""Relay Account controller."""

import frappe
from frappe.model.document import Document


class RelayAccount(Document):
	"""A configured messaging channel account."""

	def validate(self):
		if not self.api_url:
			self.api_url = "https://graph.facebook.com"
		if not self.api_version:
			self.api_version = "v20.0"

	def get_access_token(self) -> str:
		"""Return decrypted access token."""
		return self.get_password("access_token")

	def get_api_base_url(self) -> str:
		"""Return provider API base URL."""
		return f"{self.api_url.rstrip('/')}/{self.api_version}"


@frappe.whitelist()
def get_default_account(direction: str = "outgoing") -> str | None:
	"""Return name of the default account for the given direction."""
	field = "is_default_incoming" if direction == "incoming" else "is_default_outgoing"
	return frappe.db.get_value(
		"Relay Account",
		{field: 1, "status": "Active"},
		"name",
	)
