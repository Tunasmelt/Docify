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
