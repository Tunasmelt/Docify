#!/usr/bin/env bash
# feature-check — Verify feature registry completeness
set -euo pipefail
FEATURES=".agent/FEATURES.md"
[[ ! -f "$FEATURES" ]] && echo "No FEATURES.md found" && exit 1

echo "Feature Registry Check"
echo "────────────────────────────────"
TOTAL=0; COMPLETE=0; TESTED=0; PLANNED=0; IN_PROGRESS=0

while IFS= read -r line; do
  [[ "$line" =~ ^##+\ \[FEAT- ]] && TOTAL=$((TOTAL+1))
  [[ "$line" =~ ^\*\*Status:\*\*[[:space:]]*complete ]] && COMPLETE=$((COMPLETE+1))
  [[ "$line" =~ ^\*\*Status:\*\*[[:space:]]*tested ]] && TESTED=$((TESTED+1)) && COMPLETE=$((COMPLETE+1))
  [[ "$line" =~ ^\*\*Status:\*\*[[:space:]]*planned ]] && PLANNED=$((PLANNED+1))
  [[ "$line" =~ ^\*\*Status:\*\*[[:space:]]*in-progress ]] && IN_PROGRESS=$((IN_PROGRESS+1))
done < "$FEATURES"

echo "Total features:    $TOTAL"
echo "Planned:           $PLANNED"
echo "In progress:       $IN_PROGRESS"
echo "Complete:          $COMPLETE"
echo "Fully tested:      $TESTED"
echo ""
echo "Run /gap-check for full gap analysis."
