#!/usr/bin/env bash
# ralph-loop.sh — Autonomous build loop over FEATURES.md, with a real verifier.
#
# Each iteration: pick ONE incomplete feature, run the agent on it with fresh
# context (Ralph's context-reset principle), then INDEPENDENTLY verify with
# tests + gap-check. Commit only on green. Loop to the next feature. State lives
# on disk and in git, never in a growing conversation.
#
# The verifier is deliberately separate from the agent — the driver grades the
# work, not the model that wrote it. That separation is the whole reliability story.
#
# Usage:
#   ralph-loop.sh --dry-run                 # preview prompts, invoke nothing (DO THIS FIRST)
#   ralph-loop.sh                           # run the loop with detected agent
#   ralph-loop.sh --max 5                   # cap iterations (default 10)
#   ralph-loop.sh --retries 2               # attempts per story before stalling (default 3)
#   ralph-loop.sh --verify "npm test"       # override the verifier command
#   ralph-loop.sh --agent "claude -p"       # override the agent invocation
#   ralph-loop.sh --story FEAT-003          # loop on one specific story only
#
# SAFETY: start with --dry-run. Loops burn tokens fast on vague goals. Tight
# acceptance criteria in FEATURES.md are what keep this bounded.

set -uo pipefail

FEATURES=".agent/FEATURES.md"
RALPH_DIR=".agent/ralph"
PROGRESS="$RALPH_DIR/progress.txt"
VERIFY_LOG="$RALPH_DIR/last-verify.log"

MAX_ITERATIONS=10
MAX_RETRIES=3
VERIFY_CMD=""
AGENT_CMD=""
DRY_RUN=false
ONLY_STORY=""

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --max)     MAX_ITERATIONS="$2"; shift 2 ;;
    --retries) MAX_RETRIES="$2"; shift 2 ;;
    --verify)  VERIFY_CMD="$2"; shift 2 ;;
    --agent)   AGENT_CMD="$2"; shift 2 ;;
    --story)   ONLY_STORY="$2"; shift 2 ;;
    --help)    grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -28; exit 0 ;;
    *) shift ;;
  esac
done

if [ -t 1 ]; then
  GRN="\033[0;32m"; YLW="\033[0;33m"; RED="\033[0;31m"; CYN="\033[0;36m"; BLD="\033[1m"; RST="\033[0m"
else
  GRN=""; YLW=""; RED=""; CYN=""; BLD=""; RST=""
fi

mkdir -p "$RALPH_DIR"
log() {
  local msg="$1"
  echo -e "$msg"
  echo "[$(date -u +%H:%M:%S)] $(echo -e "$msg" | sed 's/\x1b\[[0-9;]*m//g')" >> "$PROGRESS"
}

# ── Preconditions ─────────────────────────────────────────────────────────────
[[ -f "$FEATURES" ]] || { echo "No $FEATURES — run agent-os bootstrap first."; exit 1; }

# Default verifier: project tests + gap-check (independent of the agent).
if [[ -z "$VERIFY_CMD" ]]; then
  if [[ -f package.json ]] && grep -q '"test"' package.json; then
    VERIFY_CMD="npm test"
  else
    VERIFY_CMD="true"  # no test script; gap-check still runs below
  fi
fi

# Default agent: Claude Code headless if present.
if [[ -z "$AGENT_CMD" ]]; then
  if command -v claude >/dev/null 2>&1; then
    AGENT_CMD="claude -p"
  else
    AGENT_CMD=""
  fi
fi

# ── Story selection from FEATURES.md ──────────────────────────────────────────
# Next incomplete story = first FEAT block whose Status is planned or in-progress.
next_story() {
  if [[ -n "$ONLY_STORY" ]]; then
    # Only return it while it's still incomplete.
    local status
    status=$(awk -v id="$ONLY_STORY" '
      index($0,"## ["id"]"){g=1}
      g && /^\*\*Status:\*\*/ {sub(/.*Status:\*\*[[:space:]]*/,"");gsub(/[[:space:]]/,"");print;exit}
    ' "$FEATURES")
    [[ "$status" == "planned" || "$status" == "in-progress" || -z "$status" ]] && echo "$ONLY_STORY"
    return
  fi
  awk '
    /^##+ \[FEAT-/ { id=$0; sub(/^##+ \[/,"",id); sub(/\].*/,"",id); story=id }
    /^\*\*Status:\*\*/ {
      s=$0; sub(/.*Status:\*\*[[:space:]]*/,"",s); gsub(/[[:space:]]/,"",s)
      if ((s=="planned" || s=="in-progress") && story!="") { print story; exit }
    }
  ' "$FEATURES"
}

# Extract one story block (header until the next FEAT header).
extract_story() {
  awk -v id="$1" '
    index($0,"[" id "]") { grab=1; print; next }
    grab && /^##+ \[FEAT-/ { exit }
    grab { print }
  ' "$FEATURES"
}

# ── Prompt construction (the fixed anchor set, reset every iteration) ─────────
build_prompt() {
  local story_id="$1" failure="$2"
  echo "You are running ONE iteration of an autonomous build loop."
  echo "Do exactly one unit of work: implement the single feature below, verify it, then stop."
  echo "Do NOT start any other feature. Do NOT refactor unrelated code."
  echo ""
  echo "## Rules"
  sed -n '/^# §RULES/,/^# §SESSION/p' AGENT.md 2>/dev/null | grep -E '^- ' | head -20
  echo ""
  echo "## The ONE feature to implement this iteration"
  extract_story "$story_id"
  echo ""
  if [[ -n "$failure" ]]; then
    echo "## Your previous attempt FAILED verification. Read the output and fix it:"
    echo '```'
    echo "$failure" | tail -35
    echo '```'
    echo ""
  fi
  echo "## Steps this iteration"
  echo "1. Write tests from the acceptance criteria FIRST (test-driven)."
  echo "   Use: bash .agent/scripts/test-scaffold.sh $story_id  (if available)"
  echo "2. Implement the feature until the tests pass."
  echo "3. Run the verifier yourself: $VERIFY_CMD"
  echo "4. Run: bash .agent/scripts/gap-check.sh"
  echo "5. If green: set this feature's Status to 'tested' in $FEATURES,"
  echo "   and write a CHANGELOG.md entry."
  echo "6. Stop. The driver will verify independently and commit if you passed."
}

# ── Independent verifier (driver-run, NOT self-graded by the agent) ──────────
run_verifier() {
  : > "$VERIFY_LOG"
  echo "\$ $VERIFY_CMD" >> "$VERIFY_LOG"
  eval "$VERIFY_CMD" >> "$VERIFY_LOG" 2>&1 || return 1
  if [[ -f .agent/scripts/gap-check.sh ]]; then
    echo "\$ gap-check" >> "$VERIFY_LOG"
    local crit
    crit=$(bash .agent/scripts/gap-check.sh 2>/dev/null | grep -c "CRITICAL" | head -1 | tr -d '[:space:]')
    crit="${crit:-0}"
    echo "gap-check critical count: $crit" >> "$VERIFY_LOG"
    [[ "$crit" -eq 0 ]] || return 1
  fi
  return 0
}

# ── The loop ──────────────────────────────────────────────────────────────────
echo -e "${BLD}ralph-loop${RST}  max=$MAX_ITERATIONS retries=$MAX_RETRIES"
echo -e "verify: ${CYN}$VERIFY_CMD${RST} + gap-check"
echo -e "agent:  ${CYN}${AGENT_CMD:-<none — dry-run only>}${RST}"
echo "────────────────────────────────"

if [[ "$DRY_RUN" == false && -z "$AGENT_CMD" ]]; then
  echo -e "${RED}No agent CLI found (looked for 'claude').${RST}"
  echo "Either install Claude Code, pass --agent \"<cmd>\", or use --dry-run."
  exit 1
fi

iteration=0
last_story=""
retry=0
last_failure=""

while [[ "$iteration" -lt "$MAX_ITERATIONS" ]]; do
  story="$(next_story)"
  if [[ -z "$story" ]]; then
    log "${GRN}✓ All stories complete. Loop finished cleanly.${RST}"
    break
  fi

  # Stall detection: same story failing repeatedly.
  if [[ "$story" == "$last_story" ]]; then
    retry=$((retry + 1))
  else
    retry=0
    last_story="$story"
    last_failure=""
  fi
  if [[ "$retry" -ge "$MAX_RETRIES" ]]; then
    log "${RED}✗ STALL: $story failed $MAX_RETRIES times. Stopping for human review.${RST}"
    log "  See $VERIFY_LOG for the last failure."
    break
  fi

  iteration=$((iteration + 1))
  log "${BLD}── iteration $iteration — $story (attempt $((retry + 1))/$MAX_RETRIES) ──${RST}"

  prompt="$(build_prompt "$story" "$last_failure")"

  if [[ "$DRY_RUN" == true ]]; then
    echo ""
    echo "$prompt"
    echo ""
    echo -e "${YLW}--- [dry-run] would pipe the above into: $AGENT_CMD ---${RST}"
    echo -e "${YLW}--- [dry-run] then run: $VERIFY_CMD + gap-check ---${RST}"
    echo -e "${YLW}--- [dry-run] stopping after one preview. Remove --dry-run to run for real. ---${RST}"
    break
  fi

  # Reset context: fresh agent invocation each iteration.
  echo "$prompt" | eval "$AGENT_CMD" || log "${YLW}agent exited non-zero (continuing to verify)${RST}"

  # Independent verification.
  if run_verifier; then
    log "${GRN}✓ verification PASSED for $story${RST}"
    if command -v git >/dev/null 2>&1 && git rev-parse --git-dir >/dev/null 2>&1; then
      git add -A
      git commit -m "feat($story): implemented via ralph loop [iter $iteration]" >/dev/null 2>&1 \
        && log "  committed on green" || log "  nothing to commit"
    fi
    last_failure=""
    retry=0
    last_story=""   # force re-scan for the next incomplete story
  else
    log "${RED}✗ verification FAILED for $story (attempt $((retry + 1)))${RST}"
    last_failure="$(cat "$VERIFY_LOG" 2>/dev/null)"
  fi
done

if [[ "$iteration" -ge "$MAX_ITERATIONS" ]]; then
  log "${YLW}Reached max iterations ($MAX_ITERATIONS). Stopping. Run again to continue.${RST}"
fi

echo "────────────────────────────────"
echo "Progress log: $PROGRESS"
echo "Run: bash .agent/scripts/ralph-status.sh  for a summary."
