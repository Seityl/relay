# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Terminal refusals in the outbound queue (Seityl/relay#7).

A provider refusal that is a property of the request or the account --
compliance gates, invalid recipients, unsubscribed contacts, a closed
session window -- is certain to fail again. Before #7 every failure rode the
RETRY_DELAYS backoff: five billable calls with a guaranteed outcome. These
tests pin the split: PermanentRejection lands `Refused` and is never
re-selected nor resurrected by retry_failed; a transient failure still
gets the backoff.

The rows are made through send_message -- the path every producer uses --
not hand-inserted, so the tests drive the product's own queueing.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from relay.integrations.base_adapter import PermanentRejection

CONTACT_PHONE = "15557002222"


class TestOutboundQueueRefusal(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		cls.contact = frappe.get_doc(
			{
				"doctype": "Relay Contact",
				"display_name": "Queue Refusal Test",
				"identifiers": [
					{
						"identifier_type": "Phone",
						"identifier_value": CONTACT_PHONE,
						"is_primary": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		cls.account = frappe.get_doc(
			{
				"doctype": "Relay Account",
				"account_name": "Queue Refusal Twilio",
				"status": "Active",
				"channel": "Twilio",
			}
		).insert(ignore_permissions=True)

	def setUp(self):
		# IntegrationTestCase rolls back per class, not per test.
		frappe.db.savepoint("queue_refusal")
		self.addCleanup(frappe.db.rollback, save_point="queue_refusal")

	def _queue_a_freeform(self):
		"""A queued freeform message via the product's own send path.

		The session is opened by an inbound message seconds old, so the
		pre-queue window check passes and the row is queued for real.
		"""
		from relay.relay.doctype.relay_message.relay_message import send_message

		thread = frappe.get_doc(
			{
				"doctype": "Relay Thread",
				"contact": self.contact.name,
				"account": self.account.name,
				"status": "Open",
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Relay Message",
				"thread": thread.name,
				"contact": self.contact.name,
				"account": self.account.name,
				"direction": "Incoming",
				"status": "Delivered",
				"content_type": "text",
				"message_body": "from the customer",
			}
		).insert(ignore_permissions=True)

		with patch("frappe.db.commit"):
			send_message(
				recipient_type="Phone",
				recipient_value=CONTACT_PHONE,
				message_body="hello",
				account=self.account.name,
			)

		rows = frappe.get_all(
			"Relay Outbound Queue",
			filters={"contact": self.contact.name, "status": "Queued"},
			fields=["name"],
			order_by="creation desc",
			limit=1,
		)
		return rows[0].name, thread.name

	def _process(self, queue_name, adapter_send=None):
		"""Run one queue entry with the provider stubbed at the registry."""
		from relay.queue.outbound_queue import _process_single

		if adapter_send is None:
			adapter_send = MagicMock(return_value="SM-fake-provider-id")

		with (
			patch("frappe.db.commit"),
			patch(
				"relay.queue.outbound_queue.get_adapter",
				return_value=MagicMock(send=adapter_send),
			),
		):
			result = _process_single(queue_name)
		return result, adapter_send

	def _row(self, queue_name):
		return frappe.db.get_value(
			"Relay Outbound Queue",
			queue_name,
			["status", "attempts", "next_attempt_at", "error_log"],
			as_dict=True,
		)

	def test_a_permanent_rejection_lands_refused_and_is_never_retried(self):
		queue_name, _thread = self._queue_a_freeform()

		def refuse(queue_doc):
			raise PermanentRejection(
				"Twilio rejected the request (63016): Outside messaging window"
			)

		result, _ = self._process(queue_name, adapter_send=refuse)

		self.assertEqual(result, "refused")
		row = self._row(queue_name)
		self.assertEqual(row.status, "Refused")
		self.assertIn("63016", row.error_log)

		# The selector takes only Queued rows, so a second drain never
		# touches the refusal: the provider is not billed for a call whose
		# outcome cannot change.
		with (
			patch("frappe.db.commit"),
			patch("relay.queue.outbound_queue.get_adapter") as get_adapter,
		):
			frappe.db.set_value(
				"Relay Outbound Queue", queue_name, "next_attempt_at", now_datetime()
			)
			from relay.queue.outbound_queue import process_queue

			process_queue()
			get_adapter.assert_not_called()

	def test_a_transient_failure_still_gets_the_backoff(self):
		queue_name, _thread = self._queue_a_freeform()

		result, _ = self._process(
			queue_name, adapter_send=MagicMock(side_effect=Exception("provider hiccup"))
		)

		self.assertEqual(result, "failed")
		row = self._row(queue_name)
		self.assertEqual(row.status, "Queued")
		self.assertEqual(row.attempts, 1)
		expected = add_to_date(now_datetime(), seconds=60)
		self.assertLessEqual(
			(abs((row.next_attempt_at - expected).total_seconds())), 5,
			"first retry should ride the 60 s delay",
		)

	def test_retry_failed_does_not_resurrect_refused_rows(self):
		queue_name, _thread = self._queue_a_freeform()

		def refuse(queue_doc):
			raise PermanentRejection("Twilio rejected the request (20003): Permission Denied")

		self._process(queue_name, adapter_send=refuse)

		with patch("frappe.db.commit"):
			from relay.queue.outbound_queue import retry_failed

			outcome = retry_failed()

		self.assertEqual(outcome, {"retried": 0})
		self.assertEqual(self._row(queue_name).status, "Refused")

	def test_a_refused_row_marks_its_message_failed(self):
		"""A message that can never be sent must not read Pending."""
		queue_name, thread = self._queue_a_freeform()

		def refuse(queue_doc):
			raise PermanentRejection("Twilio rejected the request (21610): unsubscribed")

		self._process(queue_name, adapter_send=refuse)

		status = frappe.db.get_value(
			"Relay Message",
			{"thread": thread, "direction": "Outgoing"},
			"status",
			order_by="creation desc",
		)
		self.assertEqual(status, "Failed")

	def test_a_row_aged_out_of_window_while_queued_is_refused_at_dispatch(self):
		"""The window is re-checked at dispatch: queued inside it, the
		session can close before the provider is ever called."""
		queue_name, _thread = self._queue_a_freeform()

		# The customer's inbound ages past the window while the row waits.
		frappe.db.set_value(
			"Relay Message",
			{"thread": frappe.db.get_value("Relay Outbound Queue", queue_name, "thread"),
			 "direction": "Incoming"},
			"creation",
			add_to_date(now_datetime(), days=-4),
		)

		with (
			patch("frappe.db.commit"),
			patch("relay.queue.outbound_queue.get_adapter") as get_adapter,
		):
			from relay.queue.outbound_queue import _process_single

			result = _process_single(queue_name)

		self.assertEqual(result, "refused")
		row = self._row(queue_name)
		self.assertEqual(row.status, "Refused")
		self.assertEqual(row.attempts, 0, "no attempt may be spent on a refusal")
		get_adapter.assert_not_called()

	def test_a_lost_message_write_does_not_lose_the_refusal(self):
		"""The queue row's terminal status is the critical write; a failure
		while marking the linked message is logged, not fatal (#6's lesson)."""
		queue_name, thread = self._queue_a_freeform()

		def refuse(queue_doc):
			raise PermanentRejection("Twilio rejected the request (21211): Invalid 'To' number")

		with (
			patch("frappe.db.commit"),
			patch(
				"relay.queue.outbound_queue.get_adapter",
				return_value=MagicMock(send=refuse),
			),
			patch(
				"relay.queue.outbound_queue._mark_linked_message_failed",
				side_effect=Exception("cannot reach the message"),
			),
			patch("frappe.log_error") as log_error,
		):
			from relay.queue.outbound_queue import _process_single

			result = _process_single(queue_name)

		self.assertEqual(result, "refused")
		self.assertEqual(self._row(queue_name).status, "Refused")
		self.assertEqual(self._row(queue_name).attempts, 1)
		self.assertTrue(log_error.called, "the lost message write must be recorded")
