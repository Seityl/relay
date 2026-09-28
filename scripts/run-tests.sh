#!/usr/bin/env bash
# Run an app's Frappe test suite on a dedicated test site, and only there.
#
#   scripts/run-tests.sh [--site SITE] [--app APP] [-- extra run-tests args]
#
# Defaults: --site relay.test --app relay. Any site whose name does not end
# in ".test" is refused: the suite commits rows, and a site that holds real
# data is not a place to find that out.
#
# With no extra args this is a full run: every test category, with a JUnit
# report (one document per category), then checked against the app's
# known-failures list (<app>/tests/known_failures.txt), and the exit code is
# the ratchet's -- see <app>/tests/known_failures.py. With extra args (e.g.
# `-- --module relay.tests.test_hooks`) it is a targeted run and the exit code
# is bench's. Full output goes to logs/tests/ in the app, never truncated.

set -uo pipefail

SITE="relay.test"
APP="relay"
EXTRA=()

while [[ $# -gt 0 ]]; do
	case "$1" in
		--site) SITE="$2"; shift 2 ;;
		--app) APP="$2"; shift 2 ;;
		--) shift; EXTRA=("$@"); break ;;
		*) EXTRA+=("$1"); shift ;;
	esac
done

if [[ "$SITE" != *.test ]]; then
	echo "refused: '$SITE' is not a test site (a test site's name ends in .test)." >&2
	echo "The suite writes and commits rows; run it on rxflow.test or relay.test." >&2
	exit 2
fi

BENCH_DIR="${BENCH_DIR:-$(cd "$(dirname "$0")/../../.." && pwd)}"
if [[ ! -d "$BENCH_DIR/sites/$SITE" ]]; then
	echo "refused: site '$SITE' does not exist under $BENCH_DIR/sites." >&2
	exit 2
fi

ALLOW_TESTS=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("allow_tests"))' \
	"$BENCH_DIR/sites/$SITE/site_config.json")
if [[ "$ALLOW_TESTS" != "1" && "$ALLOW_TESTS" != "True" ]]; then
	echo "refused: allow_tests is not set on '$SITE' (got '$ALLOW_TESTS')." >&2
	echo "Set it with: bench --site $SITE set-config allow_tests 1" >&2
	exit 2
fi

LOG_DIR="$BENCH_DIR/apps/$APP/logs/tests"
mkdir -p "$LOG_DIR"
STAMP="$SITE-$APP-$(date +%Y%m%dT%H%M%S)"

summarise() {
	# Every summary line the runner printed, not just the last one.
	grep -nE '^(Ran [0-9]+ tests?|OK|FAILED)' "$1" || true
	echo "log=$1 lines=$(wc -l <"$1")"
}

if [[ ${#EXTRA[@]} -gt 0 ]]; then
	LOG="$LOG_DIR/$STAMP.log"
	echo "site=$SITE app=$APP targeted run: ${EXTRA[*]}"
	(cd "$BENCH_DIR" && bench --site "$SITE" run-tests --app "$APP" "${EXTRA[@]}") >"$LOG" 2>&1
	STATUS=$?
	summarise "$LOG"
	echo "exit=$STATUS"
	exit "$STATUS"
fi

LOG="$LOG_DIR/$STAMP.log"
XML="$LOG_DIR/$STAMP.xml"
(cd "$BENCH_DIR" && bench --site "$SITE" run-tests --app "$APP" --junit-xml-output "$XML") >"$LOG" 2>&1
echo "bench exit=$?"
summarise "$LOG"
if [[ ! -s "$XML" ]]; then
	echo "no JUnit report at $XML (is unittest-xml-reporting installed?)" >&2
	exit 1
fi

KNOWN="$BENCH_DIR/apps/$APP/$APP/tests/known_failures.txt"
[[ -f "$KNOWN" ]] || : >"$LOG_DIR/$STAMP-empty-known-failures.txt"
[[ -f "$KNOWN" ]] || KNOWN="$LOG_DIR/$STAMP-empty-known-failures.txt"
"$BENCH_DIR/env/bin/python" "$BENCH_DIR/apps/$APP/$APP/tests/known_failures.py" "$KNOWN" "$XML"
STATUS=$?
echo "exit=$STATUS"
exit "$STATUS"
