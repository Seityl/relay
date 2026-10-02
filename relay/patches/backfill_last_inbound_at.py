# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Backfill `last_inbound_at` on Relay Thread from each thread's history (#7).

The field is written from now on by `RelayThread.update_timestamps`; threads
that predate it get their stamp from the last Incoming message they hold. A
thread with no incoming message keeps the field unset: no session ever opened
on it, and the backfill must not invent one.

The UPDATE is one statement so a site with many threads does not pay one
transaction per row, and `modified` is left alone: a backfill is not thread
activity, and bumping it would reorder the inbox and trip optimistic locks.
"""

import frappe


def execute():
	frappe.db.sql(
		"""
		update `tabRelay Thread` t
		set t.last_inbound_at = (
			select max(m.creation)
			from `tabRelay Message` m
			where m.thread = t.name and m.direction = 'Incoming'
		)
		where t.last_inbound_at is null
		"""
	)
