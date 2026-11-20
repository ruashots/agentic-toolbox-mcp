#!/bin/sh
# Install the frozen Camoufox browser where camoufox-js looks for it
# (/root/.cache/camoufox), the same way camoufox-js's own "fetch" does it:
# unpack the release zip, write version.json, make everything executable.
#
# Also installs the two files camoufox-js would otherwise download the first
# time the browser starts: the uBlock Origin addon and the GeoIP database.
# With all three in the image, nothing is installed or updated at run time.
#
# Inputs (Dockerfile ARGs): CAMOUFOX_VERSION, CAMOUFOX_RELEASE, CAMOUFOX_SHA256,
# UBO_URL, UBO_SHA256. The browser zip is read from /vendor when it is there
# (gateway/vendor/ in the repo), otherwise downloaded from the official release.
set -eu

dir=/root/.cache/camoufox
zip_name="camoufox-${CAMOUFOX_VERSION}-${CAMOUFOX_RELEASE}-lin.x86_64.zip"
url="https://github.com/daijro/camoufox/releases/download/v${CAMOUFOX_VERSION}-${CAMOUFOX_RELEASE}/${zip_name}"

if [ -f "/vendor/${zip_name}" ]; then
    zip="/vendor/${zip_name}"
    echo "browser: using gateway/vendor/${zip_name}"
else
    zip=/tmp/camoufox.zip
    echo "browser: downloading ${url}"
    if ! curl -fsSL --retry 3 --retry-delay 5 -o "$zip" "$url"; then
        echo "browser: download failed. Get ${zip_name} (sha256 ${CAMOUFOX_SHA256})," >&2
        echo "browser: put it in gateway/vendor/ and build again." >&2
        exit 1
    fi
fi

# The build stops here if the file is not exactly the frozen build.
echo "${CAMOUFOX_SHA256}  ${zip}" | sha256sum -c -

rm -rf "$dir"
mkdir -p "$dir"
python3 -m zipfile -e "$zip" "$dir"
if [ "$zip" = /tmp/camoufox.zip ]; then rm -f "$zip"; fi
printf '{"version":"%s","release":"%s"}' "$CAMOUFOX_VERSION" "$CAMOUFOX_RELEASE" > "$dir/version.json"

# uBlock Origin: camoufox-js loads it by default.
curl -fsSL --retry 3 --retry-delay 5 -o /tmp/ubo.xpi "$UBO_URL"
echo "${UBO_SHA256}  /tmp/ubo.xpi" | sha256sum -c -
mkdir -p "$dir/addons/UBO"
python3 -m zipfile -e /tmp/ubo.xpi "$dir/addons/UBO"
rm -f /tmp/ubo.xpi
test -f "$dir/addons/UBO/manifest.json"

# uBlock Origin keeps the filter lists it shipped with. Left alone it downloads about
# ten lists from CDNs each time the browser starts (every start is a fresh profile).
# The setting reaches it through Firefox's enterprise policies.
python3 - "$dir/distribution/policies.json" <<'POLICY'
import json, sys
path = sys.argv[1]
doc = json.load(open(path))
policies = doc["policies"]
if "3rdparty" in policies:
    sys.exit("install-browser: policies.json already has a 3rdparty section; merge it by hand")
policies["3rdparty"] = {"Extensions": {"uBlock0@raymondhill.net": {"userSettings": [["autoUpdate", "false"]]}}}
json.dump(doc, open(path, "w"), indent=2)
POLICY

# GeoIP database: camoufox-mcp-server turns geoip on by default, so camoufox-js
# matches the browser's time zone and language to the machine's public IP.
# This is the one file that is not frozen: its source (the mirror camoufox-js
# itself uses) keeps only its last two releases, so the build takes the current one.
curl -fsSL --retry 3 --retry-delay 5 -o "$dir/GeoLite2-City.mmdb" \
    https://github.com/P3TERX/GeoLite.mmdb/releases/latest/download/GeoLite2-City.mmdb
grep -q 'MaxMind.com' "$dir/GeoLite2-City.mmdb"

chmod -R 755 "$dir"
test -x "$dir/camoufox-bin"
echo "browser: installed Camoufox ${CAMOUFOX_VERSION}-${CAMOUFOX_RELEASE} with uBlock Origin and GeoIP data"
