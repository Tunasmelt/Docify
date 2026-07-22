#!/usr/bin/env bash
# ctx-scope — Set session file scope
[[ $# -eq 0 ]] && echo "Usage: ctx-scope.sh <path1> [path2...]" && exit 0
[[ "${1:-}" == "--clear" ]] && rm -f .agent/scope.json && echo "✓ Scope cleared" && exit 0
PATHS=("$@")
JSON_PATHS=$(printf '"%s",' "${PATHS[@]}"); JSON_PATHS="[${JSON_PATHS%,}]"
echo "{\"set_at\":\"$(date -u +"%Y-%m-%dT%H:%M:%SZ")\",\"paths\":$JSON_PATHS}" > .agent/scope.json
echo "✓ Scope set — ${#PATHS[@]} path(s): ${PATHS[*]}"
echo "# Paste into session: 'This session is scoped to: ${PATHS[*]}'"
