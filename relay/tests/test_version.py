"""One version for relay, announced on every response.

Clause guarded: the version a running relay reports (the X-Relay-Version
header) is `relay.__version__`, the only place the number is written.
"""

import ast
from pathlib import Path

import frappe
from frappe.tests import UnitTestCase

import relay


class _Response:
	def __init__(self):
		self.headers = {}


class TestOneVersion(UnitTestCase):
	def test_one_version(self):
		init = Path(relay.__file__)
		assigned = [
			node.value.value
			for node in ast.walk(ast.parse(init.read_text()))
			if isinstance(node, ast.Assign)
			and any(getattr(t, "id", None) == "__version__" for t in node.targets)
		]
		self.assertEqual(assigned, [relay.__version__], "__version__ is written once, in relay/__init__.py")

	def test_every_response_announces_the_version(self):
		self.assertIn("relay.version.add_version_header", frappe.get_hooks("after_request", app_name="relay"))
		from relay.version import add_version_header

		response = _Response()
		add_version_header(response=response)
		self.assertEqual(response.headers.get("X-Relay-Version"), relay.__version__)
