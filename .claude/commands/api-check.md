Run: bash .agent/scripts/api-check.sh $ARGUMENTS
If a doc is FRESH, read the cached .agent/api-docs/<api>.md and proceed — do not re-fetch.
If MISSING or STALE: read the installed version (--versions), fetch official live docs
with your web tool, write .agent/api-docs/<api>.md with today's verified date and the
exact version, then code against those verified signatures.
Never write API integration code from memory.
