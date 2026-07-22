#!/usr/bin/env bash
# ctx-dump — Prompt agent to write HANDOFF.md
DATE=$(date -u +"%Y-%m-%d %H:%M UTC")
SESSION_NUM=$(grep -c "^## Session" HANDOFF.md 2>/dev/null || echo "0")
SESSION_NUM=$((SESSION_NUM + 1))
cat << PROMPT
# /ctx-dump — Write HANDOFF.md now

Write to HANDOFF.md using EXACTLY this format (under 400 words total):

## Session $SESSION_NUM — $DATE

### State
[2–4 sentences: what is done, what is in progress]

### Decisions
- [decision]: [reason]

### Next steps
1. [Specific step]
2. [Specific step]

### Gotchas
- [thing next session must know]

### Files touched
- [path]

After writing, run: bash .agent/scripts/ctx-dump.sh --validate
PROMPT

if [[ "${1:-}" == "--validate" ]]; then
  [[ ! -f "HANDOFF.md" ]] && echo "✗ HANDOFF.md missing" && exit 1
  grep -q "### Next steps" HANDOFF.md && echo "✓ HANDOFF.md valid" || echo "✗ Missing sections"
fi
