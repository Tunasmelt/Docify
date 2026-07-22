#!/usr/bin/env bash
# ralph-status.sh — Summarise the current state of the build loop.
# Usage: bash .agent/scripts/ralph-status.sh

set -uo pipefail
FEATURES=".agent/FEATURES.md"
PROGRESS=".agent/ralph/progress.txt"

if [ -t 1 ]; then
  GRN="\033[0;32m"; YLW="\033[0;33m"; CYN="\033[0;36m"; BLD="\033[1m"; RST="\033[0m"
else GRN=""; YLW=""; CYN=""; BLD=""; RST=""; fi

echo -e "${BLD}Ralph loop status${RST}"
echo "────────────────────────────────"

# Feature progress from FEATURES.md
if [[ -f "$FEATURES" ]]; then
  total=$(grep -cE "^##+ \[FEAT-" "$FEATURES" 2>/dev/null | tr -d "[:space:]"); total=${total:-0}
  done_=$(grep -cE "^\*\*Status:\*\*[[:space:]]*(tested|complete)" "$FEATURES" 2>/dev/null | tr -d "[:space:]"); done_=${done_:-0}
  prog=$(grep -cE "^\*\*Status:\*\*[[:space:]]*in-progress" "$FEATURES" 2>/dev/null | tr -d "[:space:]"); prog=${prog:-0}
  plan=$(grep -cE "^\*\*Status:\*\*[[:space:]]*planned" "$FEATURES" 2>/dev/null | tr -d "[:space:]"); plan=${plan:-0}
  echo -e "Features:  ${GRN}$done_ done${RST} · ${YLW}$prog in-progress${RST} · $plan planned · $total total"
  if [[ "$total" -gt 0 ]]; then
    pct=$(( done_ * 100 / total ))
    filled=$(( pct / 5 )); bar=""
    for ((i=0;i<20;i++)); do [[ $i -lt $filled ]] && bar+="█" || bar+="░"; done
    echo -e "Progress:  [$bar] ${pct}%"
  fi
else
  echo "No FEATURES.md found."
fi

echo ""
# Next story up
if [[ -f "$FEATURES" ]]; then
  next=$(awk '
    /^##+ \[FEAT-/ { id=$0; sub(/^##+ \[/,"",id); sub(/\].*/,"",id); story=id }
    /^\*\*Status:\*\*/ { s=$0; sub(/.*Status:\*\*[[:space:]]*/,"",s); gsub(/[[:space:]]/,"",s)
      if ((s=="planned"||s=="in-progress")&&story!=""){print story;exit} }
  ' "$FEATURES")
  [[ -n "$next" ]] && echo -e "Next up:   ${CYN}$next${RST}" || echo -e "${GRN}Next up:   nothing — all features done${RST}"
fi

echo ""
# Recent loop activity
if [[ -f "$PROGRESS" ]]; then
  echo -e "${BLD}Recent iterations${RST}"
  tail -12 "$PROGRESS"
else
  echo "No loop has run yet. Start with: bash .agent/scripts/ralph-loop.sh --dry-run"
fi
