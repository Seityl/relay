# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Intent classification and automated reply engine.

Rules are configured in the Relay Auto Reply Rule DocType and evaluated in
priority order. The first matching rule decides the response and any side
effects (assignment, status change, tags).
"""

import re
from dataclasses import dataclass, field
from typing import Any

import frappe
from frappe.utils.safe_exec import get_safe_globals, safe_exec

from relay.compliance.consent import process_opt_in, process_stop_request
from relay.relay.doctype.relay_message.relay_message import send_message


@dataclass
class IntentResult:
    """Result of intent classification."""

    rule_name: str = ""
    intent: str = ""
    matched: bool = False
    response_sent: bool = False
    response_message_id: str = ""
    actions: dict = field(default_factory=dict)


STOP_KEYWORDS = {
    "stop",
    "unsubscribe",
    "cancel",
    "opt out",
    "opt-out",
    "dont message",
    "don't message",
    "no more messages",
}

START_KEYWORDS = {
    "start",
    "subscribe",
    "opt in",
    "opt-in",
    "resubscribe",
}

HELP_KEYWORDS = {
    "help",
    "support",
    "assistance",
    "contact",
}

REFILL_KEYWORDS = {
    "refill",
    "renew",
    "repeat prescription",
    "more medication",
    "need more",
}

PRESCRIPTION_KEYWORDS = {
    "prescription",
    "script",
    "rx",
    "doctor said",
    "my meds",
    "medication",
}

DELIVERY_KEYWORDS = {
    "delivery",
    "where is my order",
    "track",
    "shipping",
    "driver",
    "delivered",
}

GREETING_KEYWORDS = {
    "hi",
    "hello",
    "hey",
    "good morning",
    "good afternoon",
    "good evening",
}

COMPLAINT_KEYWORDS = {
    "complaint",
    "bad",
    "terrible",
    "wrong",
    "missing",
    "not working",
    "unhappy",
    "frustrated",
}

INTENT_KEYWORDS = {
    "Stop": STOP_KEYWORDS,
    "Start": START_KEYWORDS,
    "Help": HELP_KEYWORDS,
    "Refill": REFILL_KEYWORDS,
    "Prescription": PRESCRIPTION_KEYWORDS,
    "Delivery": DELIVERY_KEYWORDS,
    "Greeting": GREETING_KEYWORDS,
    "Complaint": COMPLAINT_KEYWORDS,
}


def classify_message(message_doc, thread_doc=None, contact_doc=None) -> IntentResult:
    """Classify an inbound message and execute the matching auto-reply rule."""
    thread_doc = thread_doc or _get_thread(message_doc.thread)
    contact_doc = contact_doc or _get_contact(message_doc.contact)

    detected_intent = _detect_intent_from_text(message_doc.message_body or "")

    # Do not auto-respond to blocked contacts unless they are trying to opt back in.
    if contact_doc and contact_doc.is_blocked and detected_intent != "Start":
        return IntentResult(matched=False, intent=detected_intent)

    rules = _get_active_rules(message_doc.account)

    for rule in rules:
        if _rule_matches(rule, message_doc, thread_doc, contact_doc, detected_intent):
            return _execute_rule(rule, message_doc, thread_doc, contact_doc)

    # Evaluate fallback rules last
    for rule in rules:
        if rule.fallback_when_no_match:
            return _execute_rule(rule, message_doc, thread_doc, contact_doc)

    return IntentResult(matched=False, intent=detected_intent)


def send_auto_reply(
    message_doc,
    response_type: str,
    template: str = "",
    response_subject: str = "",
    response_body: str = "",
) -> dict:
    """Send an automated reply to the contact."""
    contact_doc = _get_contact(message_doc.contact)
    identifier = contact_doc.get_primary_identifier()
    if not identifier:
        return {"success": False, "error": "No primary identifier on contact"}

    if response_type == "Template" and template:
        return send_message(
            recipient_type=identifier.identifier_type,
            recipient_value=identifier.identifier_value,
            template=template,
            reference_doctype="Relay Message",
            reference_name=message_doc.name,
        )

    if response_type == "Freeform":
        return send_message(
            recipient_type=identifier.identifier_type,
            recipient_value=identifier.identifier_value,
            subject=response_subject,
            message_body=_strip_html(response_body) or response_body,
            html_body=response_body,
            reference_doctype="Relay Message",
            reference_name=message_doc.name,
        )

    return {"success": False, "error": "No response configured"}


def _get_active_rules(account_name: str):
    """Return enabled auto-reply rules for an account, ordered by priority."""
    filters = {"enabled": 1}
    if account_name:
        account = frappe.get_doc("Relay Account", account_name)
        filters["channel"] = ["in", [account.channel, ""]]

    return frappe.get_all(
        "Relay Auto Reply Rule",
        filters=filters,
        fields=["*"],
        order_by="priority asc, creation asc",
    )


def _detect_intent_from_text(text: str) -> str:
    """Best-effort intent detection from message text."""
    lower = text.lower()
    scores = {}
    for intent, keywords in INTENT_KEYWORDS.items():
        scores[intent] = sum(1 for kw in keywords if kw in lower)

    if not scores or max(scores.values()) == 0:
        return "Question" if "?" in text else "Other"

    return max(scores, key=scores.get)


def _rule_matches(rule, message_doc, thread_doc, contact_doc, detected_intent: str) -> bool:
    """Check if a rule matches the inbound message."""
    if rule.fallback_when_no_match:
        return False

    if rule.business_hours_only and not _is_business_hours():
        return False

    text = (message_doc.message_body or "") + " " + (message_doc.subject or "")

    if rule.match_type == "Always":
        matched = True
    elif rule.match_type == "Keyword":
        keywords = [kw.strip().lower() for kw in (rule.keywords or "").split(",") if kw.strip()]
        matched = any(kw in text.lower() for kw in keywords)
    elif rule.match_type == "Regex":
        try:
            matched = bool(re.search(rule.regex_pattern or "", text, re.IGNORECASE))
        except re.error:
            matched = False
    elif rule.match_type == "Intent":
        matched = rule.intent == detected_intent
    else:
        matched = False

    if matched and rule.condition:
        matched = _evaluate_condition(rule.condition, message_doc, thread_doc, contact_doc)

    return matched


def _execute_rule(rule, message_doc, thread_doc, contact_doc) -> IntentResult:
    """Execute the actions and response defined by a rule."""
    result = IntentResult(rule_name=rule.name, intent=rule.intent or "", matched=True)

    account = message_doc.account if message_doc else ""
    channel = ""
    if account:
        try:
            channel = frappe.db.get_value("Relay Account", account, "channel") or ""
        except Exception:
            channel = ""

    # Opt-in must be applied before the welcome response is sent, otherwise the
    # consent check on outbound messages would block it.
    if rule.intent == "Start":
        process_opt_in(contact_doc, channel=channel, account=account, message_doc=message_doc)

    # Apply thread-level actions (status, assignment, tags)
    if thread_doc:
        if rule.set_thread_status:
            thread_doc.status = rule.set_thread_status
        if rule.assign_to:
            thread_doc.assigned_to = rule.assign_to
        if rule.add_tags:
            tags = [t.strip() for t in (rule.add_tags or "").split(",") if t.strip()]
            existing = {t.strip() for t in (thread_doc.get("tags") or "").split(",") if t.strip()}
            existing.update(tags)
            thread_doc.tags = ", ".join(sorted(existing))
        if thread_doc.has_value_changed("status") or thread_doc.has_value_changed("assigned_to") or thread_doc.has_value_changed("tags"):
            thread_doc.save(ignore_permissions=True)

    # Send automated response
    if rule.response_type and rule.response_type != "None":
        response = send_auto_reply(
            message_doc,
            rule.response_type,
            template=rule.template,
            response_subject=rule.response_subject,
            response_body=rule.response_body,
        )
        result.response_sent = response.get("success", False)
        result.response_message_id = response.get("message_id", "")

    # Opt-out is applied after the goodbye response so the final confirmation
    # can still be delivered.
    if rule.intent == "Stop":
        process_stop_request(contact_doc, channel=channel, account=account, message_doc=message_doc)

    result.actions = {
        "status": rule.set_thread_status,
        "assigned_to": rule.assign_to,
        "tags": rule.add_tags,
    }

    return result


def _evaluate_condition(condition: str, message_doc, thread_doc, contact_doc) -> bool:
    """Evaluate a Python condition against message context."""
    try:
        result = safe_exec(
            f"result = bool({condition})",
            get_safe_globals(),
            {
                "message": message_doc.as_dict(),
                "thread": thread_doc.as_dict() if thread_doc else {},
                "contact": contact_doc.as_dict() if contact_doc else {},
            },
        )
        return result.get("result", False) if isinstance(result, dict) else bool(result)
    except Exception:
        frappe.log_error(title="Relay Auto Reply Condition Failed")
        return False


def _is_business_hours() -> bool:
    """Return whether current time is within configured business hours."""
    # TODO: make configurable per account
    from datetime import datetime

    now = datetime.now()
    return 0 <= now.weekday() <= 4 and 8 <= now.hour < 18


def _get_thread(name: str):
    if not name:
        return None
    return frappe.get_doc("Relay Thread", name)


def _get_contact(name: str):
    if not name:
        return None
    return frappe.get_doc("Relay Contact", name)


def _strip_html(value: str) -> str:
    """Return plain text without HTML tags."""
    if not value:
        return ""
    try:
        from frappe.utils import strip_html

        return strip_html(value)
    except Exception:
        return re.sub(r"<[^>]+>", "", value)
