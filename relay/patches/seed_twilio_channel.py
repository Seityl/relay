# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Seed the Twilio channel on sites that already have relay installed (#5).

`after_install` only runs on a fresh install, so adding an entry to
install.CHANNELS does nothing for a site relay is already on. This patch is
what makes the new channel appear there.

It reads the same CHANNELS list rather than restating it. The existing
`migrate_v0_2_medium_agnostic` patch keeps its own copy of that list, which
has already drifted once -- it does not set `webhook_path` on the update
branch -- and two declarations of the same configuration is how the next
drift happens.
"""

import frappe

from relay.install.install import CHANNELS

CHANNEL_NAME = "Twilio"


def execute():
	channel = next((c for c in CHANNELS if c["channel_name"] == CHANNEL_NAME), None)
	if not channel:
		frappe.log_error(
			title="Relay Twilio Channel Patch",
			message=f"{CHANNEL_NAME} is no longer in install.CHANNELS; nothing seeded",
		)
		return

	if frappe.db.exists("Relay Channel", CHANNEL_NAME):
		# Keep the adapter wiring current without touching an administrator's
		# own edits to `enabled`.
		frappe.db.set_value(
			"Relay Channel",
			CHANNEL_NAME,
			{
				"adapter_class": channel["adapter_class"],
				"handler_module": channel["handler_module"],
				"webhook_path": channel["webhook_path"],
				"supports_templates": channel["supports_templates"],
				"supports_media": channel["supports_media"],
				"supports_interactive": channel["supports_interactive"],
			},
		)
	else:
		frappe.get_doc({"doctype": "Relay Channel", **channel}).insert(ignore_permissions=True)

	frappe.db.commit()
