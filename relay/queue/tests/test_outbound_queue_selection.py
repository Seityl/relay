# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""`process_queue` offers the scheduler the right rows, and only those (#2).

The defect: the selector asked for `status in ("Queued", "Failed")`, but
`Failed` is the terminal state `_process_single` assigns once `MAX_RETRIES`
is spent -- the retry path sets `Queued` together with a future
`next_attempt_at`. `Failed` in the selector was therefore pure redundancy,
and it meant an exhausted row was re-selected, re-attempted and re-failed on
every tick, for ever. Five rows on the dev site passed 19,000 attempts each,
one every ~4 minutes since July, and were still climbing when this was
written. Against Meta that spent an unpublished app's rate limit. Against a
metered provider every pass is a billed call, which is why this had to be
fixed before the Twilio channel landed.

The two `is_picked_up` tests below are regression guards, not defect
reproductions. They exist because of a wrong turn worth recording: a queued
row carries no `next_attempt_at` -- neither the enqueue helper nor the
DocType sets one -- and `NULL <= now()` is NULL in SQL, never true, so it
looks certain that such a row can never be selected. It is not. Frappe does
not emit a plain comparison; `frappe.get_all(..., run=False)` shows what it
builds:

    WHERE `status` IN ('Queued','Failed')
      AND IFNULL(`next_attempt_at`,'0001-01-01') <= '2026-09-11 00:41:59'

The `IFNULL` wrapping reads an unset value as year 1, which always satisfies
the comparison, so the row *is* selected. Nothing in this app's own source
says so. These two tests pin that behaviour, so that a future reader who
reasons from the SQL -- as this one did -- cannot quietly "fix" a working
path into a broken one.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date

from relay.queue.outbound_queue import MAX_RETRIES, process_queue


class TestOutboundQueueSelection(IntegrationTestCase):
	"""These tests write, so they need the class that rolls back.

	`UnitTestCase` opens no transaction -- it is for tests that only read.
	Using it here left six Relay Contacts, an Account and a queue row behind
	on the site the suite was pointed at. `IntegrationTestCase` registers
	`_rollback_db` as class cleanup
	(frappe/tests/classes/integration_test_case.py:72).

	Note that the rollback is per *class*, not per test, so shared fixtures
	belong in `setUpClass`: built in `setUp` they survive into the next test,
	and `Relay Account` autonames from `account_name`, so the second insert
	raises DuplicateEntryError before any assertion is reached.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		cls.contact = frappe.get_doc(
			{
				"doctype": "Relay Contact",
				"display_name": "Queue Selection Test",
				"identifiers": [
					{
						"identifier_type": "Phone",
						"identifier_value": "15550000001",
						"is_primary": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		cls.account = frappe.get_doc(
			{
				"doctype": "Relay Account",
				"account_name": "Queue Selection Test Account",
				"status": "Active",
				# A channel that already exists as app configuration.
				"channel": "Email",
			}
		).insert(ignore_permissions=True)

	def _queue_row(self, **overrides):
		values = {
			"doctype": "Relay Outbound Queue",
			"status": "Queued",
			"account": self.account.name,
			"contact": self.contact.name,
			"message_type": "Freeform",
			"content_type": "text",
			"message_body": "hello",
			"attempts": 0,
		}
		values.update(overrides)
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def _selected(self):
		"""The rows process_queue would work on, without dispatching any.

		Returning `[]` to the caller stops `_process_single` before it can
		reach a provider, so this exercises the selector and nothing else.
		"""
		seen = []
		real_get_all = frappe.get_all

		def capture(doctype, *args, **kwargs):
			rows = real_get_all(doctype, *args, **kwargs)
			if doctype == "Relay Outbound Queue":
				seen.extend(r.name for r in rows)
				return []
			return rows

		frappe.get_all = capture
		try:
			process_queue()
		finally:
			frappe.get_all = real_get_all
		return seen

	# --- regression guards: a queued message is still dispatched ---------

	def test_a_queued_message_is_picked_up_for_dispatch(self):
		row = self._queue_row()

		self.assertIn(
			row.name,
			self._selected(),
			"a queued message was not selected for dispatch, so it would sit "
			"in Queued for ever with no error recorded anywhere",
		)

	def test_a_queued_message_with_no_next_attempt_time_is_still_picked_up(self):
		"""Frappe's IFNULL wrapping is load-bearing here.

		Nothing in relay sets `next_attempt_at` at enqueue, so every message
		reaches the selector with it unset. If this test ever goes red, the
		selector stopped tolerating NULL and no new message is being sent.
		"""
		row = self._queue_row()
		frappe.db.set_value("Relay Outbound Queue", row.name, "next_attempt_at", None)

		self.assertIsNone(
			frappe.db.get_value("Relay Outbound Queue", row.name, "next_attempt_at"),
			"the fixture for this test did not actually leave next_attempt_at unset",
		)
		self.assertIn(
			row.name,
			self._selected(),
			"a queued row with an unset next_attempt_at was stranded by the selector",
		)

	# --- #2: an exhausted message is left alone --------------------------

	def test_an_exhausted_message_is_not_retried_again(self):
		"""`Failed` is terminal. The selector must not re-offer it.

		The live row is here to keep the assertion honest. `assertNotIn`
		against an empty list passes for free, and run on its own this test
		selects nothing -- so without a row that *should* come back, a
		selector that had stopped returning anything at all would read as a
		pass. Asserting the live one is present first means the absence of
		the exhausted one is a real observation.
		"""
		exhausted = self._queue_row(status="Failed", attempts=MAX_RETRIES)
		live = self._queue_row(attempts=MAX_RETRIES - 1)

		selected = self._selected()

		self.assertIn(
			live.name,
			selected,
			"the selector returned nothing it should have, so the assertion "
			"below would have passed without observing anything",
		)
		self.assertNotIn(
			exhausted.name,
			selected,
			f"a message that already failed {MAX_RETRIES} times was selected "
			"again; this is the loop that passed 19,000 attempts, and against "
			"a metered provider every pass is a billed call",
		)

	def test_a_message_still_within_its_retries_is_selected(self):
		"""The control.

		Without it, the test above would also pass if the selector stopped
		returning anything at all.
		"""
		row = self._queue_row(attempts=MAX_RETRIES - 1)

		self.assertIn(
			row.name,
			self._selected(),
			"a message with retries remaining was not selected, so the fix "
			"for #2 has disabled retrying altogether",
		)

	def test_a_message_waiting_out_its_backoff_is_not_selected_early(self):
		"""The second control: the time filter still has to work.

		`_process_single` reschedules a failure by setting `Queued` and a
		future `next_attempt_at`. If the fix for #2 dropped the time
		condition, every failure would be retried immediately and the backoff
		in RETRY_DELAYS would be silently gone.
		"""
		row = self._queue_row(
			attempts=1,
			next_attempt_at=add_to_date(frappe.utils.now_datetime(), hours=1),
		)

		self.assertNotIn(
			row.name,
			self._selected(),
			"a message still inside its retry backoff was selected early, so "
			"RETRY_DELAYS no longer delays anything",
		)
