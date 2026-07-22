#!/usr/bin/env bash
# memory-sync — Extract session decisions → MEMORY.md
set -euo pipefail
DATE=$(date -u +"%Y-%m-%d")
MEMORY=".agent/MEMORY.md"
HANDOFF="HANDOFF.md"

[[ ! -f "$HANDOFF" ]] && echo "No HANDOFF.md found — run /ctx-dump first" && exit 1
[[ ! -f "$MEMORY" ]] && echo "No MEMORY.md found — run bootstrap first" && exit 1

# Extract decisions from handoff
DECISIONS=$(grep -A20 "### Decisions" "$HANDOFF" 2>/dev/null | grep "^- " | head -10 || echo "")
GOTCHAS=$(grep -A10 "### Gotchas" "$HANDOFF" 2>/dev/null | grep "^- " | head -5 || echo "")

# Append to MEMORY.md sections
if [[ -n "$DECISIONS" ]]; then
  # Find decisions section and append
  TMPFILE=$(mktemp)
  awk -v date="$DATE" -v decisions="$DECISIONS" '
    /^## Decisions/ { print; print ""; while ((getline line) > 0) {
      if (line ~ /^## /) { print "- " date " — " decisions; print ""; print line; break }
      else print line
    }; next }
    { print }
  ' "$MEMORY" > "$TMPFILE" && mv "$TMPFILE" "$MEMORY" || true
fi

if [[ -n "$GOTCHAS" ]]; then
  TMPFILE=$(mktemp)
  awk -v date="$DATE" -v gotchas="$GOTCHAS" '
    /^## Anti-patterns/ { print; print ""; while ((getline line) > 0) {
      if (line ~ /^## /) { print "- " date " — " gotchas; print ""; print line; break }
      else print line
    }; next }
    { print }
  ' "$MEMORY" > "$TMPFILE" && mv "$TMPFILE" "$MEMORY" || true
fi

# Add session to index
SESSION_GOAL=$(grep "^Goal:" "$HANDOFF" 2>/dev/null | sed 's/Goal: *//' | head -1 || echo "—")
echo "| $DATE | $SESSION_GOAL | Synced | $(echo "$DECISIONS" | head -1 | sed 's/- //' | cut -c1-40) |" >> "$MEMORY"

echo "✓ Session synced → MEMORY.md"
