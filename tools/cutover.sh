#!/usr/bin/env bash
# cutover.sh — flip Synod from the bridge backend (agy-cli/cliproxy-cli) to the
# durable direct backend (gemini-3/openai-cli + vendor API keys).
#
# The agy/cliproxy bridges expire ~2026-06-30. This script gates the switch on
# an offline readiness check and a live API-key preflight, then prints the
# exact export to activate direct routing. It does NOT mutate shell rc files or
# remove the bridges — that stays a deliberate manual step.
#
# Usage:
#   tools/cutover.sh            # dry-run: checks + readiness report only
#   tools/cutover.sh --apply    # checks, then export SYNOD_PROVIDER_BACKEND=direct
#                               #   for the current shell (source it: . tools/cutover.sh --apply)
#
# Exit codes: 0 ready/applied · 1 not ready · 2 internal error

# When sourced (`. tools/cutover.sh --apply`) we must NOT enable `set -e`/`exit`,
# or a later failure would kill the user's interactive shell. Detect sourcing and
# pick exit vs return accordingly.
if (return 0 2>/dev/null); then SOURCED=1; else SOURCED=0; fi
if [ "$SOURCED" = "0" ]; then set -euo pipefail; fi
_die() { echo "$1" >&2; if [ "$SOURCED" = "1" ]; then return "$2"; else exit "$2"; fi; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

APPLY=0
REQUIRE_KEYS=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --require-keys) REQUIRE_KEYS=1 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; _die "" 0 ;;
    *) _die "unknown arg: $arg" 2 ;;
  esac
done

echo "== Synod cutover: bridge → direct =="

# 1) Offline structural readiness (rosters resolve, models valid, CLIs present).
CHECK_ARGS=""
[ "$REQUIRE_KEYS" = "1" ] && CHECK_ARGS="--require-keys"
if ! "$PY" "$SCRIPT_DIR/cutover_check.py" $CHECK_ARGS; then
  _die "✗ readiness check failed — resolve gaps above before cutover." 1
  if [ "$SOURCED" = "1" ]; then return 1; fi
fi

# 2) Live API-key preflight (advisory unless --require-keys already enforced it).
missing=""
[ -z "${GEMINI_API_KEY:-}${GOOGLE_API_KEY:-}" ] && missing="GEMINI_API_KEY (or GOOGLE_API_KEY)"
[ -z "${OPENAI_API_KEY:-}" ] && missing="$missing OPENAI_API_KEY"
if [ -n "${missing// /}" ]; then
  echo "• API keys not set in this shell:$missing" >&2
  echo "  direct backend will fail at call time until these are exported." >&2
fi

if [ "$APPLY" = "1" ]; then
  export SYNOD_PROVIDER_BACKEND=direct
  echo "✓ SYNOD_PROVIDER_BACKEND=direct exported for this shell."
  echo "  (source this script to persist in the current session: . tools/cutover.sh --apply)"
else
  echo "✓ ready. To activate direct routing:"
  echo "    export SYNOD_PROVIDER_BACKEND=direct"
  echo "  Verify a tier:  python3 tools/tier_matrix.py --tier standard --backend direct"
fi
