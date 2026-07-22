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
