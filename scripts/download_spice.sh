#!/usr/bin/env bash
# Download NASA SPICE Toolkit Fortran source from NAIF
set -euo pipefail

DATA_DIR="$(cd "$(dirname "$0")/.." && pwd)/data"
SPICE_DIR="$DATA_DIR/spice"

if [ -d "$SPICE_DIR" ] && [ "$(find "$SPICE_DIR" -name '*.f' 2>/dev/null | head -1)" ]; then
    echo "SPICE source already exists at $SPICE_DIR"
    find "$SPICE_DIR" -name '*.f' | wc -l | xargs -I{} echo "  .f files: {}"
    exit 0
fi

mkdir -p "$DATA_DIR"
cd "$DATA_DIR"

# Detect platform and set URL
case "$(uname -s)" in
    Darwin) URL="https://naif.jpl.nasa.gov/pub/naif/toolkit//FORTRAN/MacIntel_OSX_gfortran_64bit/packages/toolkit.tar.Z" ;;
    Linux)  URL="https://naif.jpl.nasa.gov/pub/naif/toolkit//FORTRAN/PC_Linux_gfortran_64bit/packages/toolkit.tar.Z" ;;
    *)      echo "Unsupported platform: $(uname -s)"; exit 1 ;;
esac

echo "=== Downloading SPICE Toolkit from NAIF ==="
echo "URL: $URL"
curl -fSL --progress-bar "$URL" -o toolkit.tar.Z

echo "=== Extracting ==="
tar xzf toolkit.tar.Z

# The tarball extracts to a 'toolkit/' directory — rename to 'spice'
if [ -d "toolkit" ]; then
    mv toolkit spice
fi

rm -f toolkit.tar.Z

# Verify
echo ""
echo "=== Verification ==="
F_COUNT=$(find "$SPICE_DIR" -name "*.f" | wc -l | tr -d ' ')
INC_COUNT=$(find "$SPICE_DIR" -name "*.inc" -o -name "*.INC" | wc -l | tr -d ' ')
LOC=$(find "$SPICE_DIR" -name "*.f" -exec cat {} + | wc -l | tr -d ' ')

echo "  .f files:   $F_COUNT"
echo "  .inc files:  $INC_COUNT"
echo "  Total LOC:   $LOC"

if [ "$F_COUNT" -lt 50 ]; then
    echo "⚠ WARNING: Expected ~930 .f files, found only $F_COUNT"
    exit 1
fi

echo "✓ SPICE source ready at $SPICE_DIR"
