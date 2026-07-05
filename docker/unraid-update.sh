#!/bin/bash
# Rom Sync on Unraid without a registry: download the latest code from GitHub, build the image on
# this server and (re)start the container. Run it once to install, and again whenever you've pushed
# changes. Your catalog, ticks and downloads are kept (they live in CONFIG and DATA, not the image).
#
#   bash /path/to/appdata/rom-sync/unraid-update.sh
#
# Private GitHub repository: create a read-only token (GitHub > Settings > Developer settings >
# Fine-grained tokens, this repository only, Contents: Read) and put it in
# /path/to/appdata/rom-sync/github-token  (one line, nothing else). Public repositories need none.
set -euo pipefail

REPO="${REPO:-sireteanu/rom-sync}"             # GitHub owner/name
BRANCH="${BRANCH:-main}"
BASE="${BASE:-/path/to/appdata/rom-sync}"     # this script, the source and the config live here
DATA="${DATA:-/path/to/roms}"                 # where synced files go
PORT="${PORT:-8484}"
TZ="${TZ:-America/Toronto}"
NAME="${NAME:-rom-sync}"

SRC="$BASE/src"
mkdir -p "$BASE/config" "$DATA"

echo "Downloading $REPO ($BRANCH) from GitHub..."
auth=()
if [ -s "$BASE/github-token" ]; then
  auth=(-H "Authorization: Bearer $(tr -d '[:space:]' < "$BASE/github-token")")
fi
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
if ! curl -fsSL "${auth[@]}" -o "$tmp/src.tar.gz" \
     "https://api.github.com/repos/$REPO/tarball/$BRANCH"; then
  echo "Download failed. A private repository needs $BASE/github-token (see the top of this script)." >&2
  exit 1
fi
mkdir "$tmp/src"
tar -xzf "$tmp/src.tar.gz" -C "$tmp/src" --strip-components=1
rm -rf "$SRC"
mv "$tmp/src" "$SRC"

echo "Building the image (first time: a few minutes)..."
docker build -t "$NAME:latest" "$SRC"

echo "Starting the container..."
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart on-failure \
  -p "$PORT:8484" \
  -v "$BASE/config:/config" \
  -v "$DATA:/data" \
  -e PUID=99 -e PGID=100 -e UMASK=000 -e TZ="$TZ" \
  --label net.unraid.docker.webui="http://[IP]:[PORT:$PORT]/" \
  "$NAME:latest" >/dev/null
docker image prune -f >/dev/null || true

echo "Done: Rom Sync is running at http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT/"
