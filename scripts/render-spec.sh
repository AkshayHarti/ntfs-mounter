#!/usr/bin/env bash
# Render a spec markdown file into the standalone HTML page that sits in docs/.
#
#   scripts/render-spec.sh [src.md] [out.html]
#
# Defaults to the MCP action server spec. Requires pandoc. The output has no
# external requests; the template lives in docs/_spec-template.html.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SRC=${1:-$ROOT/docs/specs/2026-11-03-mcp-action-server.md}
OUT=${2:-$ROOT/docs/mcp-action-server.html}
TITLE=${TITLE:-MCP action server — ntfsmnt spec}

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

pandoc "$SRC" -f gfm -t html5 \
  --template="$ROOT/docs/_spec-template.html" \
  --toc --toc-depth=2 \
  --metadata title="$TITLE" --metadata lang=en \
  -o "$TMP"

# The template owns the title block; the markdown H1 and front matter would
# duplicate it. Tables need a scroll container and the sidebar link is relative
# to docs/, not to the source directory.
python3 - "$TMP" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
html = path.read_text()
start = html.index("<main>") + len("<main>")
end = html.index('<h2 id="problem">')
html = html[:start] + "\n" + html[end:]
html = html.replace("<table>", '<div class="table-wrap"><table>')
html = html.replace("</table>", "</table></div>")
html = html.replace('href="../docs/AGENT.md"', 'href="AGENT.md"')
path.write_text(html)
PY

mv "$TMP" "$OUT"
printf '%s\n' "wrote $OUT"
