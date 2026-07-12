#!/bin/sh
# Regenerate build/icon.png (512x512) from build/icon.svg.
# electron-builder derives the platform .ico/.icns from that PNG at build time,
# so this single PNG is all you need to refresh the app icon.
#
# Uses whichever SVG rasterizer is available:
#   - rsvg-convert (brew install librsvg)   [cross-platform]
#   - macOS qlmanage                        [ships with macOS]
#   - Inkscape                              [cross-platform]
set -e
cd "$(dirname "$0")/.."
SVG=build/icon.svg
OUT=build/icon.png

if command -v rsvg-convert >/dev/null 2>&1; then
  rsvg-convert -w 512 -h 512 "$SVG" -o "$OUT"
elif command -v inkscape >/dev/null 2>&1; then
  inkscape "$SVG" --export-type=png --export-filename="$OUT" -w 512 -h 512
elif command -v qlmanage >/dev/null 2>&1; then
  qlmanage -t -s 512 -o build "$SVG" >/dev/null 2>&1
  mv "build/icon.svg.png" "$OUT"
  if command -v sips >/dev/null 2>&1; then sips -z 512 512 "$OUT" >/dev/null; fi
else
  echo "No SVG rasterizer found. Install librsvg (brew install librsvg) or Inkscape." >&2
  exit 1
fi
echo "Wrote $OUT"
