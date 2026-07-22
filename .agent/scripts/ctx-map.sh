#!/usr/bin/env bash
# ctx-map — Build symbol index
ROOT=""; OUT=""
while [[ $# -gt 0 ]]; do case $1 in --root) ROOT="$2"; shift 2;; --out) OUT="$2"; shift 2;; *) [[ -z "$ROOT" ]] && ROOT="$1"; shift;; esac; done
ROOT="${ROOT:-./apps}"; OUT="${OUT:-.agent/index.json}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
IGNORE="--exclude-dir=node_modules --exclude-dir=dist --exclude-dir=.next --exclude-dir=__pycache__ --exclude-dir=.venv --exclude-dir=venv --exclude-dir=.git --exclude-dir=build --exclude-dir=.pytest_cache"
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
