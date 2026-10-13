#!/bin/bash
# Публикует текущий адрес временного туннеля Cloudflare в ветку server-url репозитория:
# приложение читает оттуда server_url.json и подключается само.
# Запускается из cron каждые 2 минуты; пушит, только когда адрес изменился.
set -euo pipefail

REPO="${POVILIKA_URL_REMOTE:-git@github.com:Talant2004/geoserver.git}"
BRANCH=server-url
WORK="${POVILIKA_URL_WORKDIR:-/data/povilika/url-publish}"
CONTAINER=povilika-quicktunnel

url=$(docker logs "$CONTAINER" 2>&1 | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' |
  grep -v '^https://api\.' | tail -1 || true)
[ -n "$url" ] || { echo "$(date -Is) туннель $CONTAINER не дал адреса"; exit 0; }
url="$url/geoserver"

mkdir -p "$WORK"
[ "$(cat "$WORK/published" 2>/dev/null)" = "$url" ] && exit 0

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
git init -q -b "$BRANCH"
git config user.name "Talant2004"
git config user.email "Talant2004@users.noreply.github.com"
printf '{\n  "url": "%s",\n  "updated": "%s"\n}\n' "$url" "$(date -Is)" > server_url.json
git add server_url.json
git commit -q -m "Адрес сервера: $url"
GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=20" git push -q -f "$REPO" "$BRANCH:$BRANCH"
echo "$url" > "$WORK/published"
echo "$(date -Is) опубликован $url"
