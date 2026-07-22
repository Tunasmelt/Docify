#!/usr/bin/env bash
# ctx-audit — Workspace health check
echo "ctx-audit — $(date -u +"%Y-%m-%d %H:%M UTC")"
echo "────────────────────────────────"
[[ -f ".agent/index.json" ]] && echo "✓ Symbol index present" || echo "⚠ No index — run /ctx-map"
[[ -f "HANDOFF.md" ]] && echo "✓ HANDOFF.md present" || echo "⚠ No HANDOFF.md — fresh start"
[[ -f ".agent/MEMORY.md" ]] && echo "✓ MEMORY.md present" || echo "⚠ No MEMORY.md — run bootstrap"
[[ -f ".agent/FEATURES.md" ]] && echo "✓ FEATURES.md present" || echo "⚠ No FEATURES.md"
[[ -f ".agent/SCOPE.md" ]] && echo "✓ SCOPE.md present" || echo "⚠ No SCOPE.md"
[[ -f "CHANGELOG.md" ]] && echo "✓ CHANGELOG.md present" || echo "⚠ No CHANGELOG.md"
command -v git &>/dev/null && {
  BRANCH=$(git branch --show-current 2>/dev/null || echo "?")
  [[ "$BRANCH" == "main" || "$BRANCH" == "master" ]] && echo "⚠ On $BRANCH — use a feature branch" || echo "✓ Branch: $BRANCH"
}
echo ""
echo "Session start checklist:"
echo "  1. Read HANDOFF.md (if exists)"
echo "  2. Read .agent/MEMORY.md §Anti-patterns"
echo "  3. State session goal"
echo "  4. Run /ctx-scope [directories]"
