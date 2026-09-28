"""The known-failures ratchet: the suite may only get better.

`known_failures.txt` lists every test that is failing today, each with its
cause and the issue that will fix it. Against one or more JUnit reports this
check fails the run when:

- a test fails that is not on the list (a regression), or
- a listed test passes (the fix landed; the entry must leave the list), or
- a listed test no longer exists (renamed or deleted; the entry is stale), or
- an entry names no issue (debt nobody owns).

	python -m relay.tests.known_failures <known_failures.txt> <junit.xml>...
"""

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ENTRY = re.compile(r"^(?P<test>[\w.]+)\s+#\s*(?P<why>.*\(#\d+\).*)$")


def read_list(path):
	entries, invalid = {}, []
	for number, raw in enumerate(Path(path).read_text().splitlines(), 1):
		line = raw.strip()
		if not line or line.startswith("#"):
			continue
		match = ENTRY.match(line)
		if not match:
			invalid.append(f"line {number}: {line!r} does not name its issue as (#N)")
			continue
		entries[match["test"]] = match["why"]
	return entries, invalid


def _documents(path):
	"""The JUnit reports in a file; bench writes one per test category, concatenated."""
	text = Path(path).read_text()
	parts = [part for part in re.split(r"(?=<\?xml)", text) if part.strip()]
	return [ET.fromstring(part.encode()) for part in parts]


def read_results(junit_paths):
	passed, failed = set(), set()
	for path in junit_paths:
		for case in (c for root in _documents(path) for c in root.iter("testcase")):
			test = f"{case.get('classname')}.{case.get('name')}"
			if any(child.tag in ("failure", "error") for child in case):
				failed.add(test)
			elif not any(child.tag == "skipped" for child in case):
				passed.add(test)
	return passed, failed


def check(list_path, junit_paths):
	"""Return the problems, one sentence each; an empty list means the run is acceptable."""
	known, problems = read_list(list_path)
	passed, failed = read_results(junit_paths)
	problems += [f"new failure: {t}" for t in sorted(failed - set(known))]
	problems += [f"now passes, remove it from the list: {t}" for t in sorted(set(known) & passed)]
	problems += [f"no longer runs, remove it from the list: {t}" for t in sorted(set(known) - passed - failed)]
	return problems


def main(argv):
	if len(argv) < 2:
		print(__doc__)
		return 2
	problems = check(argv[0], argv[1:])
	known, _ = read_list(argv[0])
	for problem in problems:
		print(problem)
	passed, failed = read_results(argv[1:])
	print(f"tests reported: {len(passed) + len(failed)} ({len(failed)} failing); "
	      f"known failures listed: {len(known)}; problems: {len(problems)}")
	return 1 if problems else 0


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))
