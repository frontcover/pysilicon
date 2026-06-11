#!/usr/bin/env bash
# Render the overview TikZ sources to committed SVGs — single source (.tex) -> artifact (.svg).
#
#   bash render.sh
#
# `salsa.tex` is a multi-figure source (one tikzpicture per page); its pages are
# exported to named SVGs (page 1 -> salsa_system.svg, page 2 -> salsa_tile.svg).
# Any other `<name>.tex` is treated as a single standalone figure -> `<name>.svg`.
#
# Toolchain: MiKTeX pdflatex + dvisvgm (pdflatex -> PDF -> dvisvgm --pdf -> SVG).
set -euo pipefail
cd "$(dirname "$0")"

compile() {  # compile <base>.tex -> <base>.pdf
  pdflatex -interaction=nonstopmode -halt-on-error "$1.tex" >/dev/null
}

# --- multi-figure source: salsa.tex -> two named SVGs (page -> name) ---
if [ -f salsa.tex ]; then
  echo "rendering salsa.tex -> salsa_system.svg (p1), salsa_tile.svg (p2) ..."
  compile salsa
  dvisvgm --pdf -p1 salsa.pdf -o salsa_system.svg >/dev/null 2>&1
  dvisvgm --pdf -p2 salsa.pdf -o salsa_tile.svg   >/dev/null 2>&1
fi

# --- generic: any other standalone <name>.tex (one figure) -> <name>.svg ---
shopt -s nullglob
for tex in *.tex; do
  base="${tex%.tex}"
  case "$base" in salsa|_*) continue ;; esac
  echo "rendering ${tex} -> ${base}.svg ..."
  compile "$base"
  dvisvgm --pdf -p1 "${base}.pdf" -o "${base}.svg" >/dev/null 2>&1
done

rm -f ./*.aux ./*.log ./*.pdf
echo "done: $(ls *.svg 2>/dev/null | tr '\n' ' ')"
