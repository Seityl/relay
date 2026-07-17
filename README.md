# Relay

Business messaging bridge for Frappe.

Relay connects Frappe and ERPNext to business messaging providers, giving you a single place to manage templates, conversations, outbound notifications, and inbound messages.

## Features

- **Multi-channel support** — designed around a channel adapter pattern. Ships with a Meta Cloud API adapter; SMS, RCS, and other providers can be added without changing your app code.
- **Threaded conversations** — every contact gets a conversation thread with full message history, read status, and unread counts.
- **Outbound queue with retry** — messages are queued, retried with exponential backoff, and moved to a failed state after max attempts.
- **Template management** — create, version, and sync approved message templates. Parameter substitution is validated before sending.
- **Automated notifications** — trigger messages from DocType events or scheduler frequencies with conditions and field mapping.
- **Inbound webhooks** — receive messages, media, interactive responses, and status callbacks via a single public endpoint.
- **Media handling** — incoming images, documents, audio, and video are downloaded and attached to messages asynchronously.
- **Multi-account** — configure multiple business accounts and set defaults for incoming/outbound routing.

## Installation

```bash
bench get-app https://github.com/Seityl/relay.git
bench --site your-site.local install-app relay
```

## Setup

1. Create a **Relay Channel** (one is seeded automatically for the Meta Cloud API).
2. Create a **Relay Account** with your provider credentials and webhook verify token.
3. Add **Relay Templates** or sync them from your provider.
4. Configure **Relay Notifications** for automated outbound messages.
5. Register the webhook URL with your provider:

```
https://your-site.com/api/method/relay.webhooks.handler.receive
```

## API

Send a message:

```python
import frappe
frappe.call(
    "relay.api.messages.send",
    phone_number="15550001111",
    template="order_confirmation",
    template_parameters={"1": "John", "2": "ORD-123"},
)
```

## License

MIT
