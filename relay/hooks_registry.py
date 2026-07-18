# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Extension point for downstream apps such as RxFlow.

Register a callback for an event and Relay will call it at the appropriate
time. Callbacks must be idempotent and should never raise exceptions (they are
wrapped in a try/except and logged).

Example::

    from relay.hooks_registry import register_hook

    def on_inbound_message(message_doc, thread_doc, contact_doc):
        if message_doc.content_type == "image":
            ...

    register_hook("inbound_message", on_inbound_message)
"""

import frappe

_HOOKS = {
	"inbound_message": [],
	"message_status": [],
	"outbound_sent": [],
}


def register_hook(event: str, callback):
	"""Register a callback for a Relay lifecycle event."""
	if event not in _HOOKS:
		raise ValueError(f"Unknown Relay hook event: {event}")
	_HOOKS[event].append(callback)


def run_hooks(event: str, *args, **kwargs):
	"""Run all registered callbacks for an event and return their results."""
	results = []
	for callback in _HOOKS.get(event, []):
		try:
			results.append(callback(*args, **kwargs))
		except Exception:
			frappe.log_error(title=f"Relay hook '{event}' failed")
	return results
