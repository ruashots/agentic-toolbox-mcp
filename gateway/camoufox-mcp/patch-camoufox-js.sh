#!/bin/sh
# Make camoufox-js 0.10.2 accept the frozen Camoufox 156.0.1-beta.33 browser.
#
# camoufox-js builds a fingerprint config and refuses to launch if any key is
# missing from the browser's own properties.json. Browser v156 removed a few
# keys the driver still generates (navigator.product, navigator.appName,
# battery:*, canvas:aaOffset, ...), so every launch failed with
# "Unknown property navigator.product in config". No camoufox-js release
# supports v156, so we drop the keys the browser does not know instead of
# failing. Type checks on keys the browser does know stay strict.
#
# The build fails if the line to patch is not found, so a driver upgrade can
# never silently skip this.
set -eu
f=$(find /opt/camoufox-mcp/node_modules/.pnpm -path '*camoufox-js@0.10.2*/dist/utils.js' | head -n1)
[ -n "$f" ] || { echo "patch: camoufox-js 0.10.2 utils.js not found" >&2; exit 1; }
needle='throw new UnknownProperty(`Unknown property ${key} in config`);'
grep -qF "$needle" "$f" || { echo "patch: expected line not found in $f" >&2; exit 1; }
sed -i 's|throw new UnknownProperty(`Unknown property ${key} in config`);|delete configMap[key]; continue; /* toolbox patch: key not supported by this browser build */|' "$f"
grep -q 'toolbox patch' "$f"
echo "patched $f"
