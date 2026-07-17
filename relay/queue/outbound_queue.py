"""Outbound message queue with retry and dead-letter handling."""

import json
from datetime import datetime, timedelta

import frappe

MAX_RETRIES = 5
RETRY_DELAYS = [60, 300, 900, 3600, 10800]  # seconds


def queue_outgoing_message(message_doc) -> str:
	"""Create a queue entry for an outgoing message."""
	doc = frappe.get_doc(
		{
			"doctype": "Relay Outbound Queue",
			"status": "Queued",
			"account": message_doc.account,
			"contact": message_doc.contact,
			"thread": message_doc.thread,
			"message_type": message_doc.message_type,
			"content_type": message_doc.content_type,
			"message_body": message_doc.message_body,
			"template": message_doc.template,
			"template_parameters": message_doc.template_parameters,
			"media_url": message_doc.media_url,
			"interactive_payload": message_doc.interactive_payload,
			"reference_doctype": message_doc.reference_doctype,
			"reference_name": message_doc.reference_name,
			"attempts": 0,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def process_queue(batch_size: int = 50) -> dict:
	"""Process queued outbound messages. Called by scheduler."""
	queued = frappe.get_all(
		"Relay Outbound Queue",
		filters={
			"status": ["in", ["Queued", "Failed"]],
			"next_attempt_at": ["<=", frappe.utils.now()],
		},
		fields=["name"],
		limit=batch_size,
		order_by="creation asc",
	)

	results = {"sent": 0, "failed": 0, "skipped": 0}
	for row in queued:
		result = _process_single(row.name)
		results[result] = results.get(result, 0) + 1

	return results


def _process_single(queue_name: str) -> str:
	"""Process one queue entry. Returns 'sent', 'failed', or 'skipped'."""
	queue_doc = frappe.get_doc("Relay Outbound Queue", queue_name)
	if queue_doc.status == "Cancelled":
		return "skipped"

	queue_doc.status = "Pending"
	queue_doc.last_attempt_at = frappe.utils.now()
	queue_doc.attempts += 1
	queue_doc.save(ignore_permissions=True)
	frappe.db.commit()

	try:
		message_id = _dispatch(queue_doc)
		queue_doc.status = "Sent"
		queue_doc.error_log = ""
		queue_doc.save(ignore_permissions=True)

		_message = frappe.get_doc("Relay Message", {"thread": queue_doc.thread, "creation": queue_doc.creation})
		# Update linked message status
		linked = frappe.get_all(
			"Relay Message",
			filters={
				"thread": queue_doc.thread,
				"direction": "Outgoing",
				"status": "Pending",
			},
			fields=["name"],
			order_by="creation desc",
			limit=1,
		)
		if linked:
			message_doc = frappe.get_doc("Relay Message", linked[0].name)
			message_doc.message_id = message_id
			message_doc.status = "Accepted"
			message_doc.sent_at = frappe.utils.now()
			message_doc.save(ignore_permissions=True)

		frappe.db.commit()
		return "sent"
	except Exception as e:
		queue_doc.error_log = frappe.get_traceback()
		if queue_doc.attempts >= MAX_RETRIES:
			queue_doc.status = "Failed"
		else:
			delay = RETRY_DELAYS[min(queue_doc.attempts - 1, len(RETRY_DELAYS) - 1)]
			queue_doc.next_attempt_at = frappe.utils.now_datetime() + timedelta(seconds=delay)
			queue_doc.status = "Queued"
		queue_doc.save(ignore_permissions=True)
		frappe.db.commit()
		return "failed"


def _dispatch(queue_doc) -> str:
	"""Route dispatch to the correct channel adapter."""
	account = frappe.get_doc("Relay Account", queue_doc.account)
	channel = frappe.get_doc("Relay Channel", account.channel)

	if channel.provider == "Meta Cloud API":
		from relay.integrations.meta_cloud_api import MetaCloudAPIAdapter

		adapter = MetaCloudAPIAdapter(account)
		return adapter.send(queue_doc)

	frappe.throw(f"Unsupported channel provider: {channel.provider}")


@frappe.whitelist()
def retry_failed() -> dict:
	"""Manually retry all failed queue entries."""
	failed = frappe.get_all("Relay Outbound Queue", filters={"status": "Failed"}, fields=["name"])
	for row in failed:
		doc = frappe.get_doc("Relay Outbound Queue", row.name)
		doc.status = "Queued"
		doc.attempts = 0
		doc.next_attempt_at = frappe.utils.now()
		doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"retried": len(failed)}
