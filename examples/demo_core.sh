#!/usr/bin/env bash
#
# Demo: aethis-cli talking to a local aethis-core server
#
# Shows three decision outcomes using the English Language requirement bundle:
#   1. Eligible     — age exemption
#   2. Not eligible — all fields fail every route
#   3. Undetermined — partial input, engine picks the next question
#
# Prerequisites:
#   - aethis-core running on localhost:8080  (cd aethis-core && make dev)
#   - An active english_language bundle in the database
#
# Usage:
#   ./examples/demo_core.sh                          # auto-detect bundle
#   ./examples/demo_core.sh <bundle_id>              # explicit bundle
#   AETHIS_BASE_URL=http://other:8080 ./examples/demo_core.sh

set -euo pipefail

BASE_URL="${AETHIS_BASE_URL:-http://localhost:8080}"
export AETHIS_BASE_URL="$BASE_URL"
export AETHIS_API_KEY="${AETHIS_API_KEY:-test}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Colours ─────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

header() { echo -e "\n${BOLD}${CYAN}━━━ $1 ━━━${RESET}\n"; }

# ── Pre-flight ──────────────────────────────────────────────
header "Pre-flight check"

if ! curl -sf "${BASE_URL}/health" > /dev/null 2>&1; then
    echo -e "${RED}aethis-core is not running at ${BASE_URL}${RESET}"
    echo "Start it with:  cd aethis-core && make dev"
    exit 1
fi
echo -e "${GREEN}aethis-core is running at ${BASE_URL}${RESET}"

# ── Resolve bundle ID ──────────────────────────────────────
if [[ -n "${1:-}" ]]; then
    BUNDLE="$1"
else
    BUNDLE=$(curl -sf "${BASE_URL}/api/rules/available-rules" \
        -H "X-API-Key: ${AETHIS_API_KEY}" \
        | python3 -c "
import json, sys
rules = json.load(sys.stdin).get('rules', [])
eng = [r for r in rules if r.get('section_id') == 'english_language' and r.get('field_count', 0) > 0]
if eng:
    print(eng[0]['identifier'])
else:
    print('')
" 2>/dev/null)
    if [[ -z "$BUNDLE" ]]; then
        echo -e "${RED}No active english_language bundle found.${RESET}"
        echo "Generate one first, or pass a bundle_id as argument."
        exit 1
    fi
fi
echo -e "Using bundle: ${BOLD}${BUNDLE}${RESET}"

# ── Set up temporary project context ───────────────────────
# aethis-cli requires an aethis.yaml in the working directory.
# We create a minimal temp project so the demo is self-contained.
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

cat > "${TMPDIR}/aethis.yaml" <<EOF
project: demo-core
api_key_env: AETHIS_API_KEY
EOF

# ── Helper: run aethis decide from temp project dir ────────
run_decide() {
    local input="$1"
    shift
    (cd "$TMPDIR" && uv run --project "$CLI_ROOT" aethis decide \
        --bundle-id "$BUNDLE" --input "$input" "$@") 2>/dev/null
}

# ── 1. Eligible (age exemption) ────────────────────────────
header "1. Eligible (age >= 65 triggers age exemption)"

run_decide '{"lang.age": 70}'

# ── 2. Not eligible (all routes exhausted) ─────────────────
header "2. Not eligible (all fields provided, no route satisfied)"

run_decide '{
    "lang.age": 30,
    "lang.nationality": "Other",
    "lang.has_uk_degree": false,
    "lang.has_mesc_degree": false,
    "lang.has_ecctis_aquals": false,
    "lang.has_ecctis_elps": false,
    "lang.selt_level": "none",
    "lang.selt_used_for_settlement": false,
    "lang.selt_within_two_years": false,
    "lang.has_medical_exemption": false,
    "lang.discretion_applied": false
}'

# ── 3. UNDETERMINED — next question (partial input) ────────
header "3. UNDETERMINED — partial input, engine suggests next question"

run_decide '{"lang.age": 30}' --explain

# ── Done ───────────────────────────────────────────────────
header "Demo complete"
echo -e "All three outcomes demonstrated against ${BOLD}${BASE_URL}${RESET}"
