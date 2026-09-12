# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Relay channel integrations.

Importing this package registers all bundled adapters via relay.integrations.registry.
"""

from relay.integrations import base_adapter, email_adapter, meta_cloud_api, registry, twilio_adapter

__all__ = ["base_adapter", "email_adapter", "meta_cloud_api", "registry", "twilio_adapter"]
