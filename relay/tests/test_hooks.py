"""Sweep: every hook relay declares is one Frappe can resolve and will fire.

Clauses guarded here:
- every dotted path relay registers in hooks resolves to a callable
  (`test_hooks_are_live`);
- every doc_events event name is one Frappe actually runs
  (`test_every_doc_event_is_one_frappe_fires`) -- `before_update` and
  `before_delete` look like events and are not;
- no doc_event is bound to a child table, whose rows never fire document
  events of their own (`test_no_doc_event_is_bound_to_a_child_table`).

Not guarded: that a handler does anything useful once it fires.
"""

import ast
import pathlib

import frappe
from frappe.tests import UnitTestCase

APP = "relay"

# Hook keys whose values are dotted paths (strings, lists, or dicts of them).
DOTTED_HOOKS = (
	"after_install",
	"before_uninstall",
	"after_migrate",
	"before_tests",
	"before_request",
	"after_request",
	"doc_events",
	"scheduler_events",
	"permission_query_conditions",
	"has_permission",
	"override_whitelisted_methods",
	"override_doctype_class",
	"on_session_creation",
	"on_login",
	"on_logout",
)


def _dotted_paths(value, where):
	if isinstance(value, str):
		yield where, value
	elif isinstance(value, list | tuple):
		for item in value:
			yield from _dotted_paths(item, where)
	elif isinstance(value, dict):
		for key, item in value.items():
			yield from _dotted_paths(item, f"{where}.{key}")


def _declared(hook):
	return frappe.get_hooks(hook, app_name=APP) or {}


def _events_frappe_runs():
	"""Every literal event name passed to run_method anywhere in the installed apps."""
	events = set()
	for app in frappe.get_installed_apps():
		for path in pathlib.Path(frappe.get_app_path(app)).rglob("*.py"):
			try:
				tree = ast.parse(path.read_text(encoding="utf-8"))
			except (SyntaxError, UnicodeDecodeError):
				continue
			for node in ast.walk(tree):
				if (
					isinstance(node, ast.Call)
					and isinstance(node.func, ast.Attribute)
					and node.func.attr == "run_method"
					and node.args
					and isinstance(node.args[0], ast.Constant)
					and isinstance(node.args[0].value, str)
				):
					events.add(node.args[0].value)
	return events


class TestHooksAreLive(UnitTestCase):
	def test_hooks_are_live(self):
		dead = []
		checked = 0
		for hook in DOTTED_HOOKS:
			for where, path in _dotted_paths(_declared(hook), hook):
				checked += 1
				try:
					target = frappe.get_attr(path)
				except Exception as e:
					dead.append(f"{where}: {path} -> {type(e).__name__}: {e}")
					continue
				if not callable(target):
					dead.append(f"{where}: {path} -> not callable")
		self.assertGreater(checked, 0, "found no relay hooks at all; the sweep is not reading them")
		self.assertEqual(dead, [], f"{len(dead)} of {checked} hook targets are dead")

	def test_every_doc_event_is_one_frappe_fires(self):
		fired = _events_frappe_runs()
		self.assertIn("on_update", fired, "the event scan found nothing; the sweep is broken")
		unknown = [
			f"{doctype}.{event}"
			for doctype, events in _declared("doc_events").items()
			for event in events
			if event not in fired
		]
		self.assertEqual(unknown, [], "these doc_events never fire in Frappe v16")

	def test_no_doc_event_is_bound_to_a_child_table(self):
		bound = [
			doctype
			for doctype in _declared("doc_events")
			if doctype != "*" and frappe.db.get_value("DocType", doctype, "istable")
		]
		self.assertEqual(bound, [], "child-table rows never fire document events on their own")

	def test_every_hooked_document_event_maps_to_a_notification_trigger(self):
		from relay.scheduler.notification_engine import EVENT_MAP

		hooked = set(_declared("doc_events").get("*", {}))
		self.assertEqual(hooked - set(EVENT_MAP), set(), "a hooked event no notification can trigger on")
