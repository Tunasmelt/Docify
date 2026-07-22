#!/usr/bin/env bash
# bootstrap.sh — Agent OS full workspace installer
# Usage: bash .agent/scripts/bootstrap.sh [--update]
# --update: skip prompts, only fill missing files
# Works with: Claude Code, Cursor, Windsurf, Copilot, any bash agent

set -euo pipefail

AGENT_DIR=".agent"
LOG_FILE="$AGENT_DIR/logs/bootstrap.log"
UPDATE_MODE=false
DATE=$(date -u +"%Y-%m-%d")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

[[ "${1:-}" == "--update" ]] && UPDATE_MODE=true

# ── Colours ───────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  GRN="\033[0;32m"; YLW="\033[0;33m"; CYN="\033[0;36m"
  BLD="\033[1m"; RST="\033[0m"
else
  GRN=""; YLW=""; CYN=""; BLD=""; RST=""
fi

log()     { echo -e "${CYN}→ $*${RST}"; echo "[INFO] $TIMESTAMP $*" >> "$LOG_FILE" 2>/dev/null || true; }
ok()      { echo -e "${GRN}✓ $*${RST}"; }
section() { echo -e "\n${BLD}━━ $* ━━${RST}"; }
ask()     { printf "${YLW}? $1: ${RST}"; read -r REPLY; echo "$REPLY"; }

# ── Create directory structure ────────────────────────────────────────────────
section "Creating workspace structure"
mkdir -p "$AGENT_DIR"/{scripts,commands,docs,agents,logs,templates}
ok "Directories created"

# ── Collect project identity (skip in update mode) ────────────────────────────
if [[ "$UPDATE_MODE" == false ]]; then
  section "Project Identity"
  echo "Answer these questions to configure your workspace."
  echo "Press Enter to accept defaults shown in [brackets]."
  echo ""

  PROJECT_NAME=$(ask "Project name [MyProject]")
  PROJECT_NAME="${PROJECT_NAME:-MyProject}"

  PROJECT_TYPE=$(ask "Type (web-app|game|cli|library|api|saas) [web-app]")
  PROJECT_TYPE="${PROJECT_TYPE:-web-app}"

  STACK=$(ask "Stack (comma-separated, e.g. Next.js 14, Supabase, TypeScript)")
  STACK="${STACK:-TypeScript}"

  PHASES=$(ask "Phases (comma-separated, e.g. 'Phase 1: Auth, Phase 2: Dashboard')")
  PHASES="${PHASES:-Phase 1: Core}"

  HARD_RULES=$(ask "Hard rules (comma-separated, e.g. 'no commits to main, no schema drops')")
  HARD_RULES="${HARD_RULES:-Never commit to main}"

else
  # Read from existing AGENT.md if present
  PROJECT_NAME=$(grep "^name:" AGENT.md 2>/dev/null | head -1 | sed 's/name: *//' | xargs || echo "MyProject")
  PROJECT_TYPE="web-app"
  STACK="TypeScript"
  PHASES="Phase 1: Core"
  HARD_RULES="Never commit to main"
  log "Update mode — using existing project identity"
fi

# ── Generate AGENT.md ─────────────────────────────────────────────────────────
section "Generating AGENT.md"
if [[ ! -f "AGENT.md" ]] || [[ "$UPDATE_MODE" == false ]]; then

# Format stack as bullets
STACK_BULLETS=$(echo "$STACK" | tr ',' '\n' | sed 's/^[[:space:]]*/- /' | head -5)

# Format hard rules as RULE lines
RULE_NUM=4
RULES_BLOCK=""
while IFS=',' read -ra RULES; do
  for rule in "${RULES[@]}"; do
    # Trim leading/trailing whitespace without xargs (which chokes on quotes)
    rule="${rule#"${rule%%[![:space:]]*}"}"
    rule="${rule%"${rule##*[![:space:]]}"}"
    [[ -z "$rule" ]] && continue
    RULES_BLOCK+=$'\n'"    > **RULE $RULE_NUM:** $rule"
    RULE_NUM=$((RULE_NUM + 1))
  done
done <<< "$HARD_RULES"

cat > AGENT.md << AGENTMD
# ⚡ COMMAND GRID — read this first, every session

| Command          | What it does                                    | Run when                                      |
|------------------|-------------------------------------------------|-----------------------------------------------|
| /ctx-search      | Semantic symbol/function search via index       | Need to find where something lives            |
| /ctx-map         | Rebuild .agent/index.json symbol index          | Files added/deleted, index stale              |
| /ctx-dump        | Write session state → HANDOFF.md                | Before /clear, end of session, context >50%   |
| /ctx-load        | Read HANDOFF.md and resume                      | Starting any session mid-task                 |
| /ctx-audit       | Context health check                            | Session start — mandatory                     |
| /ctx-scope       | Restrict file reads to specified paths          | Focused sub-task, avoid context bleed         |
| /gap-check       | Detect implementation gaps                      | Before marking any feature complete           |
| /feature-check   | Verify feature registry vs actual code          | Phase completion, before release              |
| /test-scaffold   | Generate test stubs for a feature               | After writing acceptance criteria             |
| /changelog       | Write structured changelog entry                | After any meaningful change                   |
| /memory-sync     | Sync session decisions → MEMORY.md              | End of session, after ctx-dump                |
| /api-check       | Verify live API docs before integration         | Before writing/editing any external API call  |
| /ralph-loop      | Autonomous build loop over FEATURES.md          | Run features unattended with real verifier    |

> **RULE 1:** Never read a file to find a symbol — run /ctx-search first.
> **RULE 2:** Read HANDOFF.md + MEMORY.md §Anti-patterns before starting any work.
> **RULE 3:** Never let context exceed 60% before running /ctx-dump.
> **RULE 4:** Never mark a feature complete without passing tests and /gap-check.$RULES_BLOCK

---

# §PROJECT

\`\`\`yaml
name:        $PROJECT_NAME
type:        $PROJECT_TYPE
agent-index: .agent/index.json
handoff:     HANDOFF.md
memory:      .agent/MEMORY.md
changelog:   CHANGELOG.md
scope:       .agent/SCOPE.md
features:    .agent/FEATURES.md
\`\`\`

## Stack
$STACK_BULLETS

## Architecture pointers
Read only the file relevant to your current task.

| Area            | Read this file                         |
|-----------------|----------------------------------------|
| Architecture    | .agent/ARCHITECTURE.md                 |
| Coding standards| .agent/STANDARDS.md                    |
| Feature registry| .agent/FEATURES.md                     |
| Scope + phases  | .agent/SCOPE.md                        |
| Agent memory    | .agent/MEMORY.md                       |

## Open decisions
- [ ] [Add project-specific unresolved decisions here]

## Locked decisions
- [x] [Add finalized architectural decisions here]

---

# §RULES

## Structural (enforced by hooks + scripts)
- Never commit directly to main / master
- Never read more than 3 files without /ctx-search first
- Never mark a feature complete without: tests passing + /gap-check clean + CHANGELOG entry
- Never write code against an external API without a fresh .agent/api-docs/<api>.md entry — run /api-check first
- Never change ARCHITECTURE.md locked decisions without human confirmation

## Behavioural
- Output diffs not full files for targeted edits
- Batch all questions into one message
- Skip recaps: "No recap. Proceed."
- Do not auto-install packages — list and ask
- Every decision made this session → CHANGELOG entry before session ends

---

# §SESSION

## Starting a session
\`\`\`
1. Run /ctx-audit — address all warnings before proceeding
2. Read HANDOFF.md in full (if exists)
3. Read .agent/MEMORY.md §Anti-patterns and §Open-questions
4. State session goal in one sentence
5. Run /ctx-scope [relevant directories]
\`\`\`

## Ending a session
\`\`\`
1. Run /gap-check — document any new gaps in .agent/GAPS.md
2. Run /ctx-dump — write HANDOFF.md
3. Run /memory-sync — extract decisions → MEMORY.md
4. git add .agent/ HANDOFF.md CHANGELOG.md && git commit -m "ctx: session handoff $DATE"
\`\`\`

## Task brief format (use this every time)
\`\`\`
Context: [2–3 sentences on project state]
Goal:    [one sentence — this session accomplishes X]
Files:   [explicit list]
Avoid:   [files/areas not to touch]
Phase:   [current phase from SCOPE.md]
Feature: [FEAT-XXX if applicable]
Task:    [specific instruction]
\`\`\`

<!-- Under 150 lines. All detail lives in .agent/ pointer files. -->
AGENTMD
  ok "AGENT.md generated"
fi

# ── Generate CHANGELOG.md ─────────────────────────────────────────────────────
section "Generating CHANGELOG.md"
if [[ ! -f "CHANGELOG.md" ]]; then
cat > CHANGELOG.md << CLEOF
# Changelog — $PROJECT_NAME

Append-only record of every meaningful change, decision, and feature.
Never edit past entries. Add a correction entry if something needs updating.

---

## $DATE — infra: agent-os workspace initialised
**Phase:** Setup
**Feature:** —
**Decision:** Agent OS workspace bootstrapped with full context management system.
**Changed:** AGENT.md, .agent/ directory structure, all scripts installed.
**Impact:** All future sessions use this workspace protocol.
**Rollback:** Delete .agent/ and AGENT.md.

---
CLEOF
  ok "CHANGELOG.md created"
fi

# ── Generate .agent/SCOPE.md ──────────────────────────────────────────────────
section "Generating SCOPE.md"
if [[ ! -f "$AGENT_DIR/SCOPE.md" ]]; then

# Convert phases to sections
PHASE_SECTIONS=""
IFS=',' read -ra PHASE_LIST <<< "$PHASES"
for phase in "${PHASE_LIST[@]}"; do
  phase="${phase#"${phase%%[![:space:]]*}"}"
  phase="${phase%"${phase##*[![:space:]]}"}"
  [[ -z "$phase" ]] && continue
  PHASE_SECTIONS+="## $phase
**Status:** planning

### In scope
- [ ] [Feature A] — [acceptance criteria]
- [ ] [Feature B] — [acceptance criteria]

### Explicitly out of scope
- [Things that might seem related but are excluded from this phase]

### Dependencies
- [Feature A] requires [Feature B]

---

"
done

cat > "$AGENT_DIR/SCOPE.md" << SCOPEEOF
# Scope Registry — $PROJECT_NAME

This file defines exactly what is in scope per phase.
The gap-check script reads this to detect missing implementations.
A feature listed here with no implementation = a gap.

---

$PHASE_SECTIONS
SCOPEEOF
  ok "SCOPE.md created"
fi

# ── Generate .agent/ARCHITECTURE.md ──────────────────────────────────────────
section "Generating ARCHITECTURE.md"
if [[ ! -f "$AGENT_DIR/ARCHITECTURE.md" ]]; then
cat > "$AGENT_DIR/ARCHITECTURE.md" << ARCHEOF
# Architecture — $PROJECT_NAME

## Overview
[Describe what the system does and how the major parts fit together]

## Stack
$(echo "$STACK" | tr ',' '\n' | sed 's/^[[:space:]]*/- /')

## Patterns
- [Pattern used, e.g. Repository pattern for data access]
- [Pattern used, e.g. Feature flags for progressive rollout]

## Module map
| Directory       | Owns                                  |
|-----------------|---------------------------------------|
| src/lib/        | Pure business logic, no framework deps |
| src/components/ | UI components                         |
| src/hooks/      | React hooks                           |
| src/app/        | Routing and page components           |

## Locked decisions
- [x] [Final decision — e.g. "Using Supabase, not PlanetScale"] — reason
- [x] [Final decision] — reason

## Open decisions
- [ ] [Unresolved decision — agent must ask, never assume]

## Integration points
| System | How it connects | Auth method |
|--------|----------------|-------------|
| [External system] | [REST / webhook / SDK] | [API key / OAuth] |
ARCHEOF
  ok "ARCHITECTURE.md created"
fi

# ── Generate .agent/STANDARDS.md ─────────────────────────────────────────────
section "Generating STANDARDS.md"
if [[ ! -f "$AGENT_DIR/STANDARDS.md" ]]; then
cat > "$AGENT_DIR/STANDARDS.md" << STDEOF
# Coding Standards — $PROJECT_NAME

The gap-check script enforces these. Violations go to GAPS.md.

## Naming
- **Files:** kebab-case (e.g. user-profile.ts, not userProfile.ts)
- **Functions:** camelCase (e.g. getUserById)
- **Classes/types:** PascalCase (e.g. UserProfile)
- **Constants:** UPPER_SNAKE_CASE (e.g. MAX_RETRY_COUNT)
- **DB tables:** snake_case plural (e.g. user_profiles)
- **Test files:** [name].test.ts for unit, [name].spec.ts for integration

## Structure
- One export per file for components and classes
- Barrel exports (index.ts) allowed only at directory root
- No circular imports — lib/ must not import from app/

## Error handling
- All async functions must have try/catch or propagate typed errors
- Never swallow errors silently (empty catch blocks are forbidden)
- Errors must be typed: use Result<T, E> or typed error classes
- User-facing errors must be user-friendly strings — never expose stack traces

## Logging
- Structured format: [LEVEL] [timestamp] [component] message {key: value}
- Levels: DEBUG (dev only), INFO, WARN, ERROR, FATAL
- Never use console.log in production code — use the logger utility
- Every ERROR must include context (what was being attempted + input snapshot)

## Testing
- Unit tests: every function in src/lib/ must have a test
- Integration tests: every API route must have a test
- E2E tests: every user journey defined in SCOPE.md must have a test
- Tests run before feature is marked complete — no exceptions
- No testing implementation details — test behaviour

## Git
- Branches: feat/[feature-name], fix/[issue], chore/[task]
- Commits: [type]: [description] (e.g. feat: add user auth, fix: null check on profile)
- Never commit directly to main/master
- PR required for any change to a locked module

## Forbidden patterns
- console.log in production code
- any type in TypeScript (use unknown and narrow)
- Hardcoded secrets or API keys
- Empty catch blocks
- TODO comments older than one sprint (convert to tickets)
STDEOF
  ok "STANDARDS.md created"
fi

# ── Generate .agent/FEATURES.md ───────────────────────────────────────────────
section "Generating FEATURES.md"
if [[ ! -f "$AGENT_DIR/FEATURES.md" ]]; then
cat > "$AGENT_DIR/FEATURES.md" << FEATEOF
# Feature Registry — $PROJECT_NAME

Every feature lives here. The feature-check script compares this against actual code.
A feature without matching implementation files = gap. A feature without tests = gap.

## Status values
- \`planned\` — defined but not started
- \`in-progress\` — being implemented
- \`complete\` — code written, tests passing
- \`locked\` — shipped and stable, no changes without major review

---

## [FEAT-001] [Feature Name]
**Phase:** 1
**Status:** planned
**Owner:** —
**Files:**
- \`[path/to/implementation.ts]\` — [what it does]
**Tests:**
- \`[path/to/feature.test.ts]\` — unit tests (planned)
**Acceptance criteria:**
- [ ] [Criterion 1]
- [ ] [Criterion 2]
**Run:**
- Dev: \`[how to run in development]\`
- Test: \`[test command]\`
**Changelog:** —

---
FEATEOF
  ok "FEATURES.md created"
fi

# ── Generate .agent/MEMORY.md ─────────────────────────────────────────────────
section "Generating MEMORY.md"
if [[ ! -f "$AGENT_DIR/MEMORY.md" ]]; then
cat > "$AGENT_DIR/MEMORY.md" << MEMEOF
# Agent Memory — $PROJECT_NAME

Persistent across sessions, context clears, and agent switches.
Append-only. Never delete past entries. Synced by /memory-sync at session end.

---

## Anti-patterns
Things tried and failed — do not repeat.

- $DATE — [Nothing recorded yet — first session]

---

## Decisions
Key choices made and why. Cross-referenced with CHANGELOG.md.

- $DATE — bootstrapped workspace with agent-os

---

## Assumptions
Explicit assumptions made during development. Each should be validated.

- [ ] $DATE — [assumption] — [reason made] — [how to validate]

---

## Open questions
Needs human input. Do not proceed past these without explicit answer.

- [ ] [Question that has not been answered]

---

## Confidence map
Areas where agent certainty is high vs low.

| Area | Certainty | Notes |
|------|-----------|-------|
| [System area] | HIGH / MEDIUM / LOW | [why] |

---

## Session index
Record of all sessions for traceability.

| Date | Goal | Outcome | Key decisions |
|------|------|---------|---------------|
| $DATE | Workspace bootstrap | Complete | agent-os installed |
MEMEOF
  ok "MEMORY.md created"
fi

# ── Generate GAPS.md ──────────────────────────────────────────────────────────
if [[ ! -f "$AGENT_DIR/GAPS.md" ]]; then
cat > "$AGENT_DIR/GAPS.md" << GAPEOF
# Implementation Gaps — $PROJECT_NAME

Auto-populated by /gap-check. Manually reviewed and resolved.
Clear resolved gaps by marking them [RESOLVED - date].

---
GAPEOF
  ok "GAPS.md created"
fi

# ── Install scripts ───────────────────────────────────────────────────────────
section "Installing scripts"

# Scripts are read from this skill's scripts/ directory by the agent
# The agent copies them here during bootstrap
# For standalone use, the bootstrap itself generates minimal versions

# ctx-map.sh
cat > "$AGENT_DIR/scripts/ctx-map.sh" << 'SCRIPTEOF'
#!/usr/bin/env bash
# ctx-map — Build symbol index
ROOT=""; OUT=""
while [[ $# -gt 0 ]]; do case $1 in --root) ROOT="$2"; shift 2;; --out) OUT="$2"; shift 2;; *) [[ -z "$ROOT" ]] && ROOT="$1"; shift;; esac; done
ROOT="${ROOT:-./src}"; OUT="${OUT:-.agent/index.json}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
IGNORE="--exclude-dir=node_modules --exclude-dir=dist --exclude-dir=.next --exclude-dir=__pycache__"
COUNT=0; ENTRIES=""
extract() {
  grep -rn $IGNORE -E "^export (default )?(async function|function|class|const|type|interface|enum) [A-Za-z_][A-Za-z0-9_]*" "$ROOT" --include="*.ts" --include="*.tsx" --include="*.js" --include="*.jsx" 2>/dev/null | sed -E 's/^([^:]+):([0-9]+):.*export (default )?(async function|function|class|const|type|interface|enum) ([A-Za-z_][A-Za-z0-9_]*).*/\1:\2:\5:ts/' || true
  grep -rn $IGNORE -E "^(def |class |async def )[A-Za-z_]" "$ROOT" --include="*.py" 2>/dev/null | sed -E 's/^([^:]+):([0-9]+):(def |class |async def )([A-Za-z_][A-Za-z0-9_]*).*/\1:\2:\4:py/' || true
}
while IFS=: read -r file line symbol kind; do
  [[ -z "$symbol" || -z "$file" ]] && continue
  ENTRY="{\"file\":\"$file\",\"line\":$line,\"symbol\":\"$symbol\",\"kind\":\"$kind\"}"
  ENTRIES="${ENTRIES:+$ENTRIES,}$ENTRY"; COUNT=$((COUNT+1))
done <<< "$(extract)"
mkdir -p "$(dirname "$OUT")"
echo "{\"_meta\":{\"generated\":\"$TIMESTAMP\",\"root\":\"$ROOT\",\"symbol_count\":$COUNT},\"symbols\":[$ENTRIES]}" > "$OUT"
echo "✓ Index built — $COUNT symbols → $OUT"
SCRIPTEOF

# ctx-search.sh
cat > "$AGENT_DIR/scripts/ctx-search.sh" << 'SCRIPTEOF'
#!/usr/bin/env bash
# ctx-search — Search symbol index
INDEX=".agent/index.json"; QUERY=""; KIND=""; LIMIT=15
while [[ $# -gt 0 ]]; do case $1 in --kind) KIND="$2"; shift 2;; --limit) LIMIT="$2"; shift 2;; *) QUERY="$QUERY $1"; shift;; esac; done
QUERY=$(echo "$QUERY" | xargs)
[[ -z "$QUERY" ]] && echo "Usage: ctx-search.sh <query>" && exit 1
[[ ! -f "$INDEX" ]] && echo "No index — run /ctx-map first" && exit 1
python3 - "$INDEX" "$QUERY" "$KIND" "$LIMIT" << 'PYEOF'
import json, sys
index_path, query, kind_filter, limit = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
data = json.load(open(index_path))
terms = query.lower().split()
def score(e):
    s, sym, f = 0, e["symbol"].lower(), e["file"].lower()
    for t in terms:
        if t == sym: s+=10
        elif sym.startswith(t): s+=6
        elif t in sym: s+=4
        elif t in f: s+=2
    return s
results = [e for e in data.get("symbols",[]) if score(e)>0]
if kind_filter: results = [e for e in results if e.get("kind")==kind_filter]
results.sort(key=lambda e:(-score(e),e["symbol"]))
results = results[:limit]
if not results: print(f"No matches for: {query}"); sys.exit(0)
print(f"# {len(results)} result(s) for '{query}'\n")
for e in results: print(f"{e['file']}:{e['line']} — {e['symbol']} ({e['kind']})")
PYEOF
SCRIPTEOF

# gap-check.sh
cat > "$AGENT_DIR/scripts/gap-check.sh" << 'SCRIPTEOF'
#!/usr/bin/env bash
# gap-check — Detect implementation gaps
set -euo pipefail
DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
GAP_FILE=".agent/GAPS.md"
FEATURES=".agent/FEATURES.md"
SCOPE=".agent/SCOPE.md"
INDEX=".agent/index.json"
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

# Check 1: Features with no implementation files
if [[ -f "$FEATURES" ]]; then
  echo "→ Checking feature implementation coverage..."
  while IFS= read -r line; do
    if [[ "$line" =~ ^\*\*Files:\*\* ]]; then
      FILE_SECTION=true
    elif [[ "${FILE_SECTION:-false}" == true && "$line" =~ \`([^\`]+)\` ]]; then
      fpath="${BASH_REMATCH[1]}"
      [[ ! -f "$fpath" ]] && add_gap "CRITICAL" "Feature file missing: $fpath"
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
  CHANGELOG_DATE=$(grep "^## $DATE" CHANGELOG.md 2>/dev/null | head -1 || echo "")
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
SCRIPTEOF

# feature-check.sh
cat > "$AGENT_DIR/scripts/feature-check.sh" << 'SCRIPTEOF'
#!/usr/bin/env bash
# feature-check — Verify feature registry completeness
set -euo pipefail
FEATURES=".agent/FEATURES.md"
[[ ! -f "$FEATURES" ]] && echo "No FEATURES.md found" && exit 1

echo "Feature Registry Check"
echo "────────────────────────────────"
TOTAL=0; COMPLETE=0; TESTED=0; PLANNED=0; IN_PROGRESS=0

while IFS= read -r line; do
  [[ "$line" =~ "## \[FEAT-" ]] && TOTAL=$((TOTAL+1))
  [[ "$line" =~ "Status: complete" ]] && COMPLETE=$((COMPLETE+1))
  [[ "$line" =~ "Status: tested" ]] && TESTED=$((TESTED+1)) && COMPLETE=$((COMPLETE+1))
  [[ "$line" =~ "Status: planned" ]] && PLANNED=$((PLANNED+1))
  [[ "$line" =~ "Status: in-progress" ]] && IN_PROGRESS=$((IN_PROGRESS+1))
done < "$FEATURES"

echo "Total features:    $TOTAL"
echo "Planned:           $PLANNED"
echo "In progress:       $IN_PROGRESS"
echo "Complete:          $COMPLETE"
echo "Fully tested:      $TESTED"
echo ""
echo "Run /gap-check for full gap analysis."
SCRIPTEOF

# test-scaffold.sh
cat > "$AGENT_DIR/scripts/test-scaffold.sh" << 'SCRIPTEOF'
#!/usr/bin/env bash
# test-scaffold — Generate test stubs from a feature entry
set -euo pipefail
FEAT_ID="${1:-}"
FEATURES=".agent/FEATURES.md"

[[ -z "$FEAT_ID" ]] && echo "Usage: test-scaffold.sh FEAT-001" && exit 1
[[ ! -f "$FEATURES" ]] && echo "No FEATURES.md found" && exit 1

# Extract feature name and criteria
FEAT_NAME=$(grep -A1 "## \[$FEAT_ID\]" "$FEATURES" 2>/dev/null | head -1 | sed "s/## \[$FEAT_ID\] //")
[[ -z "$FEAT_NAME" ]] && echo "Feature $FEAT_ID not found in FEATURES.md" && exit 1

# Generate test file path
SLUG=$(echo "$FEAT_NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd '[:alnum:]-')
TEST_DIR="tests/unit"
mkdir -p "$TEST_DIR"
TEST_FILE="$TEST_DIR/$SLUG.test.ts"

# Extract acceptance criteria
CRITERIA=$(grep -A50 "## \[$FEAT_ID\]" "$FEATURES" | grep "^- \[ \]" | sed 's/- \[ \] //')

# Generate test stubs
cat > "$TEST_FILE" << TESTEOF
/**
 * Tests for [$FEAT_ID] $FEAT_NAME
 * Generated by test-scaffold.sh
 * Status: STUBS — implement before marking feature complete
 */

describe('[$FEAT_ID] $FEAT_NAME', () => {
  beforeEach(() => {
    // TODO: setup
  });

  afterEach(() => {
    // TODO: teardown
  });

TESTEOF

while IFS= read -r criterion; do
  [[ -z "$criterion" ]] && continue
  cat >> "$TEST_FILE" << CRITEOF
  // Acceptance criterion: $criterion
  it('should $(echo "$criterion" | tr '[:upper:]' '[:lower:]')', () => {
    // TODO: implement test
    expect(true).toBe(false); // force fail until implemented
  });

CRITEOF
done <<< "$CRITERIA"

echo "});" >> "$TEST_FILE"

echo "✓ Test stubs written to $TEST_FILE"
echo "  $FEAT_ID has $(echo "$CRITERIA" | grep -c . || echo 0) test(s) to implement."
echo "  Tests will FAIL until implemented — this is correct."
SCRIPTEOF

# changelog-entry.sh
cat > "$AGENT_DIR/scripts/changelog-entry.sh" << 'SCRIPTEOF'
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
SCRIPTEOF

# memory-sync.sh
cat > "$AGENT_DIR/scripts/memory-sync.sh" << 'SCRIPTEOF'
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
SCRIPTEOF

# ctx-dump.sh (minimal session-end version)
cat > "$AGENT_DIR/scripts/ctx-dump.sh" << 'SCRIPTEOF'
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
SCRIPTEOF

# ctx-audit.sh (minimal version)
cat > "$AGENT_DIR/scripts/ctx-audit.sh" << 'SCRIPTEOF'
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
SCRIPTEOF

# ctx-scope.sh (minimal)
cat > "$AGENT_DIR/scripts/ctx-scope.sh" << 'SCRIPTEOF'
#!/usr/bin/env bash
# ctx-scope — Set session file scope
[[ $# -eq 0 ]] && echo "Usage: ctx-scope.sh <path1> [path2...]" && exit 0
[[ "${1:-}" == "--clear" ]] && rm -f .agent/scope.json && echo "✓ Scope cleared" && exit 0
PATHS=("$@")
JSON_PATHS=$(printf '"%s",' "${PATHS[@]}"); JSON_PATHS="[${JSON_PATHS%,}]"
echo "{\"set_at\":\"$(date -u +"%Y-%m-%dT%H:%M:%SZ")\",\"paths\":$JSON_PATHS}" > .agent/scope.json
echo "✓ Scope set — ${#PATHS[@]} path(s): ${PATHS[*]}"
echo "# Paste into session: 'This session is scoped to: ${PATHS[*]}'"
SCRIPTEOF


# api-check.sh
cat > "$AGENT_DIR/scripts/api-check.sh" << 'APICHECK_EOF'
#!/usr/bin/env bash
# api-check.sh — Verify live API documentation before integration work.
#
# The agent fetches live docs (it has a web tool); this script handles the
# deterministic local parts: detecting which API SDKs the project uses, reading
# their INSTALLED versions, and tracking how fresh each cached doc is.
#
# Usage:
#   api-check.sh --list                 # APIs detected in this project
#   api-check.sh --status               # freshness of every cached api-doc
#   api-check.sh --versions             # installed versions of detected SDKs
#   api-check.sh --scaffold             # create stub docs for detected APIs
#   api-check.sh <api>                  # show cached doc + freshness for one API
#   api-check.sh <api> --stale-days N   # custom staleness threshold (default 30)
#
# Compatible with: Claude Code, Cursor, Windsurf, any bash-capable agent.

set -euo pipefail

DOCS_DIR=".agent/api-docs"
STALE_DAYS=30
MODE="status"
API=""

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --list)       MODE="list";     shift ;;
    --status)     MODE="status";   shift ;;
    --versions)   MODE="versions"; shift ;;
    --scaffold)   MODE="scaffold"; shift ;;
    --stale-days) STALE_DAYS="$2"; shift 2 ;;
    --help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -20
      exit 0 ;;
    -*)           shift ;;
    *)            MODE="show"; API="$1"; shift ;;
  esac
done

if [ -t 1 ]; then
  GRN="\033[0;32m"; YLW="\033[0;33m"; RED="\033[0;31m"; CYN="\033[0;36m"; BLD="\033[1m"; RST="\033[0m"
else
  GRN=""; YLW=""; RED=""; CYN=""; BLD=""; RST=""
fi

# ── Known API SDK map: package-name|Friendly Name|docs-url ────────────────────
# Extend this list freely — it only affects detection and the docs URL hint.
KNOWN_APIS="
stripe|Stripe|https://docs.stripe.com/api
@supabase/supabase-js|Supabase|https://supabase.com/docs/reference/javascript
supabase|Supabase|https://supabase.com/docs
@anthropic-ai/sdk|Anthropic|https://docs.claude.com/en/api
anthropic|Anthropic|https://docs.claude.com/en/api
@google/generative-ai|Google Gemini|https://ai.google.dev/gemini-api/docs
@google/genai|Google Gemini|https://ai.google.dev/gemini-api/docs
openai|OpenAI|https://platform.openai.com/docs/api-reference
@aws-sdk/client-s3|AWS S3|https://docs.aws.amazon.com/sdk-for-javascript
twilio|Twilio|https://www.twilio.com/docs/usage/api
@sendgrid/mail|SendGrid|https://www.twilio.com/docs/sendgrid/api-reference
resend|Resend|https://resend.com/docs/api-reference
@clerk/nextjs|Clerk|https://clerk.com/docs/references/javascript
next-auth|Auth.js|https://authjs.dev/reference
@prisma/client|Prisma|https://www.prisma.io/docs/orm
@planetscale/database|PlanetScale|https://planetscale.com/docs
mongodb|MongoDB|https://www.mongodb.com/docs/drivers/node
ioredis|Redis|https://redis.io/docs
@upstash/redis|Upstash|https://upstash.com/docs/redis
"

# ── .env prefix hints (for HTTP-only APIs with no SDK, e.g. MiniMax) ──────────
ENV_HINTS="
STRIPE_|Stripe
SUPABASE_|Supabase
ANTHROPIC_|Anthropic
GEMINI_|Google Gemini
GOOGLE_API|Google Gemini
OPENAI_|OpenAI
MINIMAX_|MiniMax
TWILIO_|Twilio
SENDGRID_|SendGrid
RESEND_|Resend
"

lookup_url() {
  local name="$1"
  echo "$KNOWN_APIS" | while IFS='|' read -r pkg friendly url; do
    [[ -z "$pkg" ]] && continue
    if [[ "$friendly" == "$name" || "$pkg" == "$name" ]]; then echo "$url"; return; fi
  done | head -1
}

# ── Detect APIs from package.json, requirements.txt, and .env ─────────────────
detect_apis() {
  local found=""

  # package.json dependencies
  if [[ -f package.json ]]; then
    local deps
    deps=$(grep -oE '"[^"]+"[[:space:]]*:' package.json | tr -d '":' | tr -d ' ' || true)
    while IFS='|' read -r pkg friendly url; do
      [[ -z "$pkg" ]] && continue
      if echo "$deps" | grep -qx "$pkg" 2>/dev/null; then
        found+="$friendly|$pkg|$url"$'\n'
      fi
    done <<< "$KNOWN_APIS"
  fi

  # requirements.txt (python)
  if [[ -f requirements.txt ]]; then
    grep -qiE '^stripe'    requirements.txt 2>/dev/null && found+="Stripe|stripe|https://docs.stripe.com/api"$'\n'
    grep -qiE '^anthropic' requirements.txt 2>/dev/null && found+="Anthropic|anthropic|https://docs.claude.com/en/api"$'\n'
    grep -qiE '^openai'    requirements.txt 2>/dev/null && found+="OpenAI|openai|https://platform.openai.com/docs/api-reference"$'\n'
    grep -qiE 'google-generativeai' requirements.txt 2>/dev/null && found+="Google Gemini|google-generativeai|https://ai.google.dev/gemini-api/docs"$'\n'
    grep -qiE 'supabase'   requirements.txt 2>/dev/null && found+="Supabase|supabase|https://supabase.com/docs"$'\n'
  fi

  # .env / .env.example prefix hints (catches HTTP-only APIs with no SDK)
  for envf in .env .env.example .env.local; do
    [[ -f "$envf" ]] || continue
    while IFS='|' read -r prefix friendly; do
      [[ -z "$prefix" ]] && continue
      if grep -qE "^${prefix}" "$envf" 2>/dev/null; then
        found+="$friendly|(env)|$(lookup_url "$friendly")"$'\n'
      fi
    done <<< "$ENV_HINTS"
  done

  # Dedupe by friendly name
  echo "$found" | awk -F'|' 'NF && !seen[$1]++'
}

# ── Read installed version of a package ───────────────────────────────────────
installed_version() {
  local pkg="$1"
  [[ "$pkg" == "(env)" ]] && { echo "http (no SDK)"; return; }
  if [[ -f "node_modules/$pkg/package.json" ]]; then
    grep '"version"' "node_modules/$pkg/package.json" | head -1 | sed -E 's/.*"version"[^"]*"([^"]+)".*/\1/'
  elif [[ -f package.json ]]; then
    grep -E "\"$pkg\"" package.json | head -1 | sed -E 's/.*"[^"]+"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/'
  else
    echo "unknown"
  fi
}

# ── Doc freshness ─────────────────────────────────────────────────────────────
doc_age_days() {
  local file="$1"
  local verified
  verified=$(grep -E '^verified:' "$file" 2>/dev/null | head -1 | sed 's/verified:[[:space:]]*//' | tr -d ' ')
  [[ -z "$verified" ]] && { echo "-1"; return; }
  if command -v python3 &>/dev/null; then
    python3 -c "
from datetime import date
try:
    y,m,d = map(int, '$verified'.split('-'))
    print((date.today() - date(y,m,d)).days)
except Exception:
    print(-1)
"
  else
    echo "0"
  fi
}

slugify() { echo "$1" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd '[:alnum:]-'; }

# ── Modes ─────────────────────────────────────────────────────────────────────
case "$MODE" in

  list)
    echo -e "${BLD}APIs detected in this project${RST}"
    echo "────────────────────────────────"
    APIS=$(detect_apis)
    [[ -z "$APIS" ]] && { echo "None detected. (Checked package.json, requirements.txt, .env)"; exit 0; }
    while IFS='|' read -r friendly pkg url; do
      [[ -z "$friendly" ]] && continue
      printf "  %-16s %s\n" "$friendly" "$url"
    done <<< "$APIS"
    ;;

  versions)
    echo -e "${BLD}Installed SDK versions${RST}"
    echo "────────────────────────────────"
    APIS=$(detect_apis)
    while IFS='|' read -r friendly pkg url; do
      [[ -z "$friendly" ]] && continue
      printf "  %-16s %-28s %s\n" "$friendly" "$pkg" "$(installed_version "$pkg")"
    done <<< "$APIS"
    ;;

  status)
    echo -e "${BLD}API doc freshness  (stale threshold: ${STALE_DAYS}d)${RST}"
    echo "────────────────────────────────"
    APIS=$(detect_apis)
    [[ -z "$APIS" ]] && { echo "No APIs detected."; exit 0; }
    NEED=0
    while IFS='|' read -r friendly pkg url; do
      [[ -z "$friendly" ]] && continue
      slug=$(slugify "$friendly")
      file="$DOCS_DIR/$slug.md"
      if [[ ! -f "$file" ]]; then
        echo -e "  ${RED}✗ MISSING${RST}  $friendly — no cached doc. Fetch + record before using."
        NEED=$((NEED+1))
      else
        age=$(doc_age_days "$file")
        if [[ "$age" -lt 0 ]]; then
          echo -e "  ${YLW}? NO DATE${RST}  $friendly — add a 'verified:' line."
          NEED=$((NEED+1))
        elif [[ "$age" -gt "$STALE_DAYS" ]]; then
          echo -e "  ${YLW}⚠ STALE${RST}    $friendly — verified ${age}d ago. Re-fetch."
          NEED=$((NEED+1))
        else
          echo -e "  ${GRN}✓ FRESH${RST}    $friendly — verified ${age}d ago."
        fi
      fi
    done <<< "$APIS"
    echo ""
    if [[ $NEED -gt 0 ]]; then
      echo -e "${YLW}$NEED API(s) need attention before integration work.${RST}"
      echo "For each: fetch live docs, then write $DOCS_DIR/<api>.md with today's verified date."
    else
      echo -e "${GRN}All detected APIs have fresh docs.${RST}"
    fi
    ;;

  scaffold)
    mkdir -p "$DOCS_DIR"
    APIS=$(detect_apis)
    CREATED=0
    while IFS='|' read -r friendly pkg url; do
      [[ -z "$friendly" ]] && continue
      slug=$(slugify "$friendly")
      file="$DOCS_DIR/$slug.md"
      [[ -f "$file" ]] && continue
      ver=$(installed_version "$pkg")
      cat > "$file" <<EOF
---
api: $friendly
sdk: $pkg
version: $ver
verified: PENDING
docs:
  - $url
---

## Version in use
$pkg@$ver

## Endpoints / methods used in this project
- [method] — [verified signature]

## Breaking changes / gotchas verified
- [recent change confirmed against live docs]

## Model strings / API identifiers (AI APIs only)
- [exact current model string — verify, do NOT trust training data]
EOF
      echo "  + created $file"
      CREATED=$((CREATED+1))
    done <<< "$APIS"
    echo ""
    echo "$CREATED stub(s) created. Fill each by fetching live docs, then set verified: to today's date."
    ;;

  show)
    slug=$(slugify "$API")
    file="$DOCS_DIR/$slug.md"
    if [[ ! -f "$file" ]]; then
      echo -e "${RED}No cached doc for '$API' at $file${RST}"
      url=$(lookup_url "$API")
      [[ -n "$url" ]] && echo "Fetch live docs from: $url"
      echo "Then write $file with today's verified date before writing code."
      exit 1
    fi
    age=$(doc_age_days "$file")
    if [[ "$age" -lt 0 ]]; then
      echo -e "${YLW}⚠ '$API' doc has no verified date — treat as unverified.${RST}"
    elif [[ "$age" -gt "$STALE_DAYS" ]]; then
      echo -e "${YLW}⚠ '$API' doc is ${age}d old (stale). Re-fetch live docs before using.${RST}"
    else
      echo -e "${GRN}✓ '$API' doc is fresh (${age}d old).${RST}"
    fi
    echo "────────────────────────────────"
    cat "$file"
    ;;
esac
APICHECK_EOF

# ralph-loop.sh
cat > "$AGENT_DIR/scripts/ralph-loop.sh" << 'RALPHLOOP_EOF'
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
    /^## \[FEAT-/ { id=$0; sub(/^## \[/,"",id); sub(/\].*/,"",id); story=id }
    /^\*\*Status:\*\*/ {
      s=$0; sub(/.*Status:\*\*[[:space:]]*/,"",s); gsub(/[[:space:]]/,"",s)
      if ((s=="planned" || s=="in-progress") && story!="") { print story; exit }
    }
  ' "$FEATURES"
}

# Extract one story block (header until the next FEAT header).
extract_story() {
  awk -v id="$1" '
    index($0,"## ["id"]") { grab=1; print; next }
    grab && /^## \[FEAT-/ { exit }
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
RALPHLOOP_EOF

# ralph-status.sh
cat > "$AGENT_DIR/scripts/ralph-status.sh" << 'RALPHSTATUS_EOF'
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
  total=$(grep -c "^## \[FEAT-" "$FEATURES" 2>/dev/null | tr -d "[:space:]"); total=${total:-0}
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
    /^## \[FEAT-/ { id=$0; sub(/^## \[/,"",id); sub(/\].*/,"",id); story=id }
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
RALPHSTATUS_EOF

chmod +x "$AGENT_DIR/scripts/"*.sh
ok "All scripts installed and executable"

# ── Generate slash commands ───────────────────────────────────────────────────
section "Generating slash commands"
mkdir -p .claude/commands

for cmd in ctx-search ctx-map ctx-dump ctx-audit gap-check feature-check memory-sync; do
cat > ".claude/commands/$cmd.md" << CMDEOF
Run: bash .agent/scripts/$cmd.sh \$ARGUMENTS
Report the output. Address any warnings before proceeding.
CMDEOF
done

cat > ".claude/commands/test-scaffold.md" << CMDEOF
Run: bash .agent/scripts/test-scaffold.sh \$ARGUMENTS
After running, read the generated test file and implement the test stubs before writing any implementation code.
CMDEOF

cat > ".claude/commands/changelog.md" << CMDEOF
Run: bash .agent/scripts/changelog-entry.sh
Fill in each field accurately. Every significant change needs a changelog entry.
CMDEOF

cat > ".claude/commands/api-check.md" << CMDEOF
Run: bash .agent/scripts/api-check.sh \$ARGUMENTS
If a doc is FRESH, read the cached .agent/api-docs/<api>.md and proceed — do not re-fetch.
If MISSING or STALE: read the installed version (--versions), fetch official live docs
with your web tool, write .agent/api-docs/<api>.md with today's verified date and the
exact version, then code against those verified signatures.
Never write API integration code from memory.
CMDEOF

cat > ".claude/commands/ralph-loop.md" << CMDEOF
Run: bash .agent/scripts/ralph-loop.sh \$ARGUMENTS
If no arguments are given, FIRST run with --dry-run and show me the prompt for
the next feature before doing any real run. Wait for my confirmation.
Report each iteration's pass/fail. If a STALL is reported, stop and summarise
the failure from .agent/ralph/last-verify.log — do not keep retrying.
CMDEOF

ok "Slash commands installed in .claude/commands/"

# ── Update .gitignore ─────────────────────────────────────────────────────────
section "Updating .gitignore"
GITIGNORE_ADDITIONS=".agent/index.json
.agent/scope.json
.agent/logs/
HANDOFF.md"

for entry in ".agent/index.json" ".agent/scope.json" ".agent/logs/" "HANDOFF.md"; do
  grep -qF "$entry" .gitignore 2>/dev/null || echo "$entry" >> .gitignore
done
ok ".gitignore updated"

# ── Run initial index ──────────────────────────────────────────────────────────
section "Building initial symbol index"
SRC_DIR="src"
[[ -d "app" && ! -d "src" ]] && SRC_DIR="app"
[[ -d "." ]] && bash "$AGENT_DIR/scripts/ctx-map.sh" --root "./$SRC_DIR" || echo "⚠ Index skipped — add source files then run /ctx-map"

# ── Scaffold API doc stubs for any detected APIs ─────────────────────────────
section "Scaffolding API doc stubs"
bash "$AGENT_DIR/scripts/api-check.sh" --scaffold 2>/dev/null || echo "⚠ No APIs detected yet — run /api-check --scaffold after adding integrations"

# ── Final audit ───────────────────────────────────────────────────────────────
section "Running workspace audit"
bash "$AGENT_DIR/scripts/ctx-audit.sh"

# ── Summary ───────────────────────────────────────────────────────────────────
section "Bootstrap complete"
echo ""
echo "Files created:"
echo "  AGENT.md              ← read this first every session"
echo "  CHANGELOG.md          ← append every meaningful change"
echo "  .agent/SCOPE.md       ← fill in phases and features"
echo "  .agent/ARCHITECTURE.md← fill in your system design"
echo "  .agent/STANDARDS.md   ← coding standards (pre-filled)"
echo "  .agent/FEATURES.md    ← add features as you build"
echo "  .agent/MEMORY.md      ← persistent agent memory"
echo "  .agent/GAPS.md        ← populated by /gap-check"
echo "  .agent/api-docs/      ← stubs for detected APIs (fill on first use)"
echo "  .agent/scripts/       ← all 13 scripts, executable"
echo "  .claude/commands/     ← slash commands for Claude Code"
echo ""
echo "Next steps:"
echo "  1. Fill in .agent/SCOPE.md with your phases and features"
echo "  2. Fill in .agent/ARCHITECTURE.md with your system design"
echo "  3. Add your first feature to .agent/FEATURES.md"
echo "  4. For each detected API, fetch live docs and fill .agent/api-docs/<api>.md"
echo "  5. Run: /test-scaffold FEAT-001 — write tests before implementation"
echo "  6. Start building — run /ctx-audit at the start of every session"
echo "  7. Once several features are well-specified: /ralph-loop --dry-run"
echo ""
echo "Command to open first session:"
echo '  "Read AGENT.md, then HANDOFF.md if it exists, then state the session goal."'
