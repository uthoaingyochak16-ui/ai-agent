#!/usr/bin/env bash
# On the server (Ubuntu with Docker), inside the unpacked move package:
#   tar -xzf server-move-XXXX.tar.gz && cd server-move-XXXX && bash restore_on_server.sh
# Puts the code in ~/assistant, the .env next to it, restores the database, starts everything, and prints the new
# public webhook address. Stops before touching anything if ~/assistant already exists.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
APP="$HOME/assistant"

if [ -e "$APP" ]; then
  echo "$APP already exists. Move it away first; nothing was changed." >&2
  exit 1
fi
command -v docker >/dev/null || { echo "Docker is not installed (see the guide)." >&2; exit 1; }

git clone -q --branch "$(cat "$HERE/BRANCH")" "$HERE/code.bundle" "$APP"
cp "$HERE/env" "$APP/.env"
chmod 600 "$APP/.env"
cd "$APP"

echo "1/4 starting the database"
docker compose up -d postgres redis
until docker compose exec -T postgres pg_isready -U app -d assistant >/dev/null 2>&1; do sleep 2; done

echo "2/4 restoring the data"
docker compose exec -T postgres pg_restore -U app -d assistant --no-owner --no-privileges --exit-on-error < "$HERE/assistant.dump"

echo "3/4 building and starting the assistant (a few minutes)"
docker compose up -d --build
until docker compose logs backend 2>&1 | grep -q "startup complete"; do sleep 3; done

echo "4/4 public address"
URL=""
for _ in $(seq 1 30); do
  URL=$(docker compose logs cloudflared 2>&1 | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | tail -1)
  [ -n "$URL" ] && break
  sleep 2
done
if [ -n "$URL" ]; then
  sed -i -E "s#^PUBLIC_BASE_URL=.*#PUBLIC_BASE_URL=$URL#" .env
  docker compose up -d --no-deps backend >/dev/null
  echo "Health: $(curl -s -m 20 "$URL/healthz" || echo 'not reachable yet')"
  echo
  echo "Done. Webhook for Meta (Messenger):  $URL/webhooks/messenger"
else
  echo "Done, but the tunnel address did not appear yet: docker compose logs cloudflared"
fi
echo "Dashboard (from your computer): ssh -L 3000:localhost:3000 root@THIS_SERVER, then open http://localhost:3000"
