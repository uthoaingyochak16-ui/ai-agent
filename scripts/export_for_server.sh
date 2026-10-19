#!/usr/bin/env bash
# Pack everything the assistant needs to run on another server, exactly as it runs here:
#   the database (knowledge, Q&A, programs, settings, AI routes, encrypted API keys, channels, guests, chats),
#   the .env file (its ENCRYPTION_KEY / HMAC_KEY decrypt the keys and phone numbers in the database),
#   and the code (a git bundle of the current branch).
# Run from the project folder:  bash scripts/export_for_server.sh
# Output: backups/server-move-<date>.tar.gz  -- it holds secrets and guests' personal data: copy it only to your own
# server (scp), never by email or chat, and delete the copies once the move is done.
set -euo pipefail
cd "$(dirname "$0")/.."

STAMP=$(date +%Y%m%d-%H%M)
OUT="backups/server-move-$STAMP"
mkdir -p "$OUT"

if [ -n "$(git status --porcelain)" ]; then
  echo "There are uncommitted changes. Commit them first so the server gets the same code." >&2
  exit 1
fi
BRANCH=$(git branch --show-current)
git bundle create "$OUT/code.bundle" "$BRANCH" >/dev/null
echo "$BRANCH" > "$OUT/BRANCH"

docker compose exec -T postgres pg_dump -U app -d assistant -Fc > "$OUT/assistant.dump"
cp .env "$OUT/env"
cp scripts/restore_on_server.sh "$OUT/"

tar -czf "$OUT.tar.gz" -C backups "server-move-$STAMP"
rm -rf "$OUT"
echo "Done: $OUT.tar.gz ($(du -h "$OUT.tar.gz" | cut -f1))"
echo "Copy it to the server:  scp $OUT.tar.gz root@YOUR_SERVER_IP:/root/"
