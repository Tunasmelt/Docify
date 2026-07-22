#!/usr/bin/env bash
# changelog-entry — Write structured changelog entry
set -euo pipefail
DATE=$(date -u +"%Y-%m-%d")
printf "Type (feature|fix|decision|refactor|test|infra|scope-change): "; read -r TYPE
printf "Title: "; read -r TITLE
printf "Phase: "; read -r PHASE
printf "Feature (FEAT-XXX or —): "; read -r FEATURE
printf "Decision/what changed: "; read -r DECISION
printf "Files changed (comma-separated): "; read -r CHANGED
printf "Impact: "; read -r IMPACT
printf "Rollback: "; read -r ROLLBACK

ENTRY="## $DATE — $TYPE: $TITLE
**Phase:** $PHASE
**Feature:** $FEATURE
**Decision:** $DECISION
**Changed:** $CHANGED
**Impact:** $IMPACT
**Rollback:** $ROLLBACK

---
"

# Append after the header line
TMPFILE=$(mktemp)
head -6 CHANGELOG.md > "$TMPFILE"
echo "" >> "$TMPFILE"
echo "$ENTRY" >> "$TMPFILE"
tail -n +7 CHANGELOG.md >> "$TMPFILE"
mv "$TMPFILE" CHANGELOG.md
echo "✓ Changelog entry written"
