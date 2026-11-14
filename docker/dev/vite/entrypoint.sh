#!/bin/sh
set -eu

# node_modules lives in a volume that outlives the image, so reinstall whenever the deps change
deps_hash="$(sha256sum package.json package-lock.json | sha256sum | cut -d ' ' -f 1)"
deps_stamp="node_modules/.argus-deps-hash"

if [ ! -f "$deps_stamp" ] || [ "$(cat "$deps_stamp")" != "$deps_hash" ]; then
    echo "Installing frontend dependencies..."
    npm ci --no-audit --no-fund --prefer-offline
    printf '%s\n' "$deps_hash" > "$deps_stamp"
fi

exec npm run dev
