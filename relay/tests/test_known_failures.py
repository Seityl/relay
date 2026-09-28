import tempfile
from pathlib import Path

from frappe.tests import UnitTestCase

from relay.tests.known_failures import check

JUNIT = """<testsuites><testsuite>
<testcase classname="m.T" name="test_ok"/>
<testcase classname="m.T" name="test_broken"><error message="boom"/></testcase>
<testcase classname="m.T" name="test_skipped"><skipped/></testcase>
</testsuite></testsuites>"""


class TestKnownFailuresRatchet(UnitTestCase):
	def _check(self, listing, junit=JUNIT):
		tmp = Path(tempfile.mkdtemp())
		(tmp / "known.txt").write_text(listing)
		(tmp / "junit.xml").write_text(junit)
		return check(tmp / "known.txt", [tmp / "junit.xml"])

	def test_the_known_failures_alone_pass_the_run(self):
		self.assertEqual(self._check("m.T.test_broken  # boom (#1)\n"), [])

	def test_a_new_failure_fails_the_run(self):
		self.assertEqual(self._check(""), ["new failure: m.T.test_broken"])

	def test_a_listed_test_that_now_passes_fails_the_run(self):
		problems = self._check("m.T.test_broken  # boom (#1)\nm.T.test_ok  # fixed? (#2)\n")
		self.assertEqual(problems, ["now passes, remove it from the list: m.T.test_ok"])

	def test_a_listed_test_that_no_longer_runs_fails_the_run(self):
		problems = self._check("m.T.test_broken  # boom (#1)\nm.T.test_gone  # renamed (#3)\n")
		self.assertEqual(problems, ["no longer runs, remove it from the list: m.T.test_gone"])

	def test_an_entry_without_an_issue_is_refused(self):
		problems = self._check("m.T.test_broken  # boom\n")
		self.assertIn("does not name its issue", problems[0])
		self.assertIn("new failure: m.T.test_broken", problems)

	def test_every_category_report_in_one_file_is_read(self):
		second = '<?xml version="1.0"?><testsuites><testsuite><testcase classname="m.U" name="test_late"><failure/></testcase></testsuite></testsuites>'
		junit = '<?xml version="1.0"?>' + JUNIT + second
		self.assertEqual(self._check("m.T.test_broken  # boom (#1)\n", junit), ["new failure: m.U.test_late"])
