# Copyright (c) 2026, Jeriel Francis (trading as Seityl) and contributors
# For license information, please see license.txt

"""Tests for the intent classification engine."""

import unittest
from types import SimpleNamespace

from relay.intent.engine import (
	_detect_intent_from_text,
	_rule_matches,
)


class TestIntentDetection(unittest.TestCase):
	"""Pure-function tests for keyword / intent detection."""

	def test_detect_stop(self):
		self.assertEqual(_detect_intent_from_text("please stop messaging me"), "Stop")

	def test_detect_refill(self):
		self.assertEqual(_detect_intent_from_text("I need a refill of my meds"), "Refill")

	def test_detect_delivery(self):
		self.assertEqual(_detect_intent_from_text("where is my delivery?"), "Delivery")

	def test_detect_question(self):
		self.assertEqual(_detect_intent_from_text("what are your opening hours?"), "Question")


class TestRuleMatching(unittest.TestCase):
	"""Pure-function tests for rule matching."""

	def _rule(self, **kwargs):
		defaults = {
			"name": "Test Rule",
			"fallback_when_no_match": 0,
			"business_hours_only": 0,
			"match_type": "Keyword",
			"keywords": "",
			"regex_pattern": "",
			"intent": "",
			"condition": "",
			"set_thread_status": "",
			"assign_to": "",
			"add_tags": "",
		}
		defaults.update(kwargs)
		return SimpleNamespace(**defaults)

	def _message(self, body: str = "", subject: str = ""):
		return SimpleNamespace(message_body=body, subject=subject)

	def test_keyword_match(self):
		rule = self._rule(match_type="Keyword", keywords="refill, repeat")
		message = self._message("I need a refill")
		self.assertTrue(_rule_matches(rule, message, None, None, "Refill"))

	def test_keyword_no_match(self):
		rule = self._rule(match_type="Keyword", keywords="refill")
		message = self._message("hello there")
		self.assertFalse(_rule_matches(rule, message, None, None, "Greeting"))

	def test_intent_match(self):
		rule = self._rule(match_type="Intent", intent="Stop")
		message = self._message("stop")
		self.assertTrue(_rule_matches(rule, message, None, None, "Stop"))

	def test_regex_match(self):
		rule = self._rule(match_type="Regex", regex_pattern=r"\brefill\b")
		message = self._message("Please refill my prescription")
		self.assertTrue(_rule_matches(rule, message, None, None, "Refill"))


if __name__ == "__main__":
	unittest.main()
