# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Adapter registry for Relay channel integrations.

Adapters register themselves by calling register() at import time. Importing
relay.integrations triggers auto-registration of all bundled adapters.
"""

import frappe

from relay.integrations.base_adapter import BaseChannelAdapter


_ADAPTERS: dict[str, type[BaseChannelAdapter]] = {}


def register(provider: str, adapter_class: type[BaseChannelAdapter]):
	"""Register an adapter class for a provider identifier."""
	if not issubclass(adapter_class, BaseChannelAdapter):
		raise TypeError(f"Adapter for {provider} must inherit from BaseChannelAdapter")
	_ADAPTERS[provider] = adapter_class


def get_adapter(provider: str, account_doc) -> BaseChannelAdapter:
	"""Return an instantiated adapter for the given provider and account."""
	if provider not in _ADAPTERS:
		frappe.throw(f"Unsupported channel provider: {provider}")
	return _ADAPTERS[provider](account_doc)


def get_registered_adapters() -> dict[str, type[BaseChannelAdapter]]:
	"""Return a copy of the registered adapter mapping."""
	return dict(_ADAPTERS)
