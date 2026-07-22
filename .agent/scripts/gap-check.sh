#!/usr/bin/env bash
# gap-check — Detect implementation gaps
# Usage: gap-check.sh [FEAT-NNN]   (optional: scope to a single feature)
set -euo pipefail
DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
DATE_ONLY=$(date -u +"%Y-%m-%d")
GAP_FILE=".agent/GAPS.md"
FEATURES=".agent/FEATURES.md"
SCOPE=".agent/SCOPE.md"
INDEX=".agent/index.json"
TARGET_FEAT="${1:-}"
GAPS=0

add_gap() {
  local severity="$1" msg="$2"
  echo "### [$severity] $msg" >> "$GAP_FILE"
  echo "Detected: $DATE" >> "$GAP_FILE"
  echo "" >> "$GAP_FILE"
  GAPS=$((GAPS+1))
  [[ "$severity" == "CRITICAL" ]] && echo "❌ CRITICAL: $msg" || echo "⚠  WARNING:  $msg"
}

echo "# Gap Check — $DATE" > "$GAP_FILE"
echo "Running gap checks..."
echo ""

# Check 1: Non-planned features with missing implementation files
# Skips features still in status "planned" — their files aren't expected to exist yet.
# Optionally scoped to a single FEAT-NNN via $1.
if [[ -f "$FEATURES" ]]; then
  echo "→ Checking feature implementation coverage..."
  CUR_FEAT=""
  CUR_STATUS=""
  FILE_SECTION=false
  while IFS= read -r line; do
    if [[ "$line" =~ ^###\ \[(FEAT-[0-9]+)\] ]]; then
      CUR_FEAT="${BASH_REMATCH[1]}"
      CUR_STATUS=""
      FILE_SECTION=false
    elif [[ "$line" == "**Status:**"* ]]; then
      CUR_STATUS="${line#\*\*Status:\*\* }"
      CUR_STATUS="${CUR_STATUS%% *}"
    elif [[ "$line" =~ ^\*\*Files:\*\* ]]; then
      FILE_SECTION=true
    elif [[ "${FILE_SECTION:-false}" == true && "$line" =~ \`([^\`]+)\` ]]; then
      if [[ "$CUR_STATUS" != "planned" && ( -z "$TARGET_FEAT" || "$CUR_FEAT" == "$TARGET_FEAT" ) ]]; then
        fpath="${BASH_REMATCH[1]}"
        [[ ! -f "$fpath" ]] && add_gap "CRITICAL" "Feature file missing: $fpath ($CUR_FEAT)"
      fi
    elif [[ "$line" =~ ^\*\* && "${FILE_SECTION:-false}" == true ]]; then
      FILE_SECTION=false
    fi
  done < "$FEATURES"
fi

# Check 2: Features marked complete without test files
if [[ -f "$FEATURES" ]]; then
  echo "→ Checking test coverage for complete features..."
  CURRENT_FEAT=""
  HAS_TESTS=false
  STATUS=""
  while IFS= read -r line; do
    [[ "$line" =~ "## [FEAT-" ]] && CURRENT_FEAT="${line#*## }" && HAS_TESTS=false
    [[ "$line" =~ "\*\*Status:\*\* complete" ]] && STATUS="complete"
    [[ "$line" =~ "\*\*Tests:\*\*" ]] && HAS_TESTS=true
    if [[ "$STATUS" == "complete" && "$HAS_TESTS" == false && -n "$CURRENT_FEAT" ]]; then
      add_gap "CRITICAL" "Feature complete with no tests: $CURRENT_FEAT"
      STATUS=""
    fi
  done < "$FEATURES"
fi

# Check 3: Scope items with no FEATURES.md entry
if [[ -f "$SCOPE" ]]; then
  echo "→ Checking scope vs feature registry..."
  while IFS= read -r line; do
    if [[ "$line" =~ "- \[ \] " ]]; then
      item="${line#*- \[ \] }"
      item="${item%% —*}"
      if [[ -f "$FEATURES" ]] && ! grep -q "$item" "$FEATURES" 2>/dev/null; then
        add_gap "WARNING" "Scope item has no FEATURES.md entry: $item"
      fi
    fi
  done < "$SCOPE"
fi

# Check 4: No CHANGELOG entry for recent git changes
if command -v git &>/dev/null && git rev-parse --git-dir &>/dev/null 2>&1; then
  echo "→ Checking changelog coverage..."
  CHANGED_FILES=$(git diff --name-only HEAD~1 HEAD 2>/dev/null | grep -v "^\.agent/" || true)
  CHANGELOG_DATE=$(grep "^## $DATE_ONLY" CHANGELOG.md 2>/dev/null | head -1 || echo "")
  if [[ -n "$CHANGED_FILES" && -z "$CHANGELOG_DATE" ]]; then
    add_gap "WARNING" "Files changed today with no CHANGELOG entry for today"
  fi
fi

# Check 5: Stale or missing API docs (any code touching external APIs must have a fresh doc)
if [[ -f .agent/scripts/api-check.sh && -d .agent/api-docs ]]; then
  echo "→ Checking API doc freshness..."
  set +e  # this section may hit SIGPIPE under strict mode; allow
  API_STATUS_OUT=$(bash .agent/scripts/api-check.sh --status 2>/dev/null)
  STALE_APIS=$(echo "$API_STATUS_OUT" | grep -cE 'STALE|MISSING|NO DATE')
  set -e
  STALE_APIS="${STALE_APIS:-0}"
  if [[ "$STALE_APIS" -gt 0 ]]; then
    add_gap "WARNING" "$STALE_APIS API doc(s) stale or missing — run /api-check"
  fi
fi

echo ""
echo "────────────────────────────────"
if [[ $GAPS -eq 0 ]]; then
  echo "✓ No gaps detected"
else
  echo "Total gaps: $GAPS — see .agent/GAPS.md"
  echo "Resolve CRITICAL gaps before marking any feature complete."
fi
