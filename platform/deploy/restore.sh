#!/usr/bin/env bash
# Restore the Poker Run SQLite database from a backup made by backup.sh.
#
#   sudo platform/deploy/restore.sh latest                       # newest object in S3
#   sudo platform/deploy/restore.sh pokerrun/db/platform-20261205T120000Z.db.gz   # an S3 key
#   sudo platform/deploy/restore.sh /srv/pokerrun/backups/platform-....db.gz      # a local file
#   add -y as the first argument to skip the confirmation prompt
#
# Steps: fetch -> gunzip to a temp file -> PRAGMA integrity_check -> stop api and
# worker -> move the current database (and its -wal/-shm) to
# ${POKERRUN_ROOT}/data/pre-restore-<time>/ -> put the backup in place (owner
# 10001) -> start api and worker. Nothing is deleted, so a restore can be undone
# by moving the pre-restore files back the same way.
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$DEPLOY_DIR/.env"
[ -f "$ENV_FILE" ] || { echo "restore: $ENV_FILE not found" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "restore: run as root (sudo)" >&2; exit 1; }

env_get() {
    local line
    line="$(grep -E "^[[:space:]]*$1=" "$ENV_FILE" | tail -n 1 || true)"
    line="${line#*=}"
    line="${line%$'\r'}"
    line="${line#\"}"; line="${line%\"}"
    line="${line#\'}"; line="${line%\'}"
    printf '%s' "$line"
}

YES=0
if [ "${1:-}" = "-y" ]; then YES=1; shift; fi
SRC="${1:-}"
[ -n "$SRC" ] || { sed -n '2,13p' "$0"; exit 2; }

BUCKET="$(env_get BACKUP_BUCKET)"
PREFIX="$(env_get BACKUP_PREFIX)"; PREFIX="${PREFIX:-pokerrun/db/}"
case "$PREFIX" in */) ;; *) PREFIX="$PREFIX/" ;; esac
ROOT="$(env_get POKERRUN_ROOT)"; ROOT="${ROOT:-/srv/pokerrun}"
REGION="$(env_get AWS_REGION)"
DATA_DIR="$ROOT/data"
DB="$DATA_DIR/platform.db"

compose() { docker compose --project-directory "$DEPLOY_DIR" -f "$DEPLOY_DIR/docker-compose.yml" "$@"; }

if [ -z "$REGION" ]; then
    TOKEN="$(curl -fsS -m 5 -X PUT http://169.254.169.254/latest/api/token \
             -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null || true)"
    [ -n "$TOKEN" ] && REGION="$(curl -fsS -m 5 -H "X-aws-ec2-metadata-token: $TOKEN" \
        http://169.254.169.254/latest/meta-data/placement/region 2>/dev/null || true)"
fi
REGION_ARGS=()
[ -n "$REGION" ] && REGION_ARGS=(--region "$REGION")

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ -f "$SRC" ]; then
    cp "$SRC" "$WORK/backup.db.gz"
    echo "restore: using local file $SRC"
else
    [ -n "$BUCKET" ] || { echo "restore: $SRC is not a file and BACKUP_BUCKET is empty" >&2; exit 1; }
    if [ "$SRC" = "latest" ]; then
        # Names sort by time (platform-YYYYmmddTHHMMSSZ.db.gz).
        NAME="$(aws s3 ls "s3://$BUCKET/$PREFIX" "${REGION_ARGS[@]}" \
                | awk '{print $4}' | grep -E '^platform-.*\.db\.gz$' | sort | tail -n 1)"
        [ -n "$NAME" ] || { echo "restore: no backups under s3://$BUCKET/$PREFIX" >&2; exit 1; }
        KEY="$PREFIX$NAME"
    else
        KEY="${SRC#s3://$BUCKET/}"
    fi
    echo "restore: downloading s3://$BUCKET/$KEY"
    aws s3 cp "s3://$BUCKET/$KEY" "$WORK/backup.db.gz" "${REGION_ARGS[@]}" --only-show-errors
fi

gunzip -c "$WORK/backup.db.gz" > "$WORK/platform.db"
python3 - "$WORK/platform.db" <<'PYEOF'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
ok = con.execute("PRAGMA integrity_check").fetchone()[0]
if ok != "ok":
    sys.exit(f"restore: integrity_check failed: {ok}")
tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables if t in ("users", "submissions", "results")}
print("restore: backup is consistent;", ", ".join(f"{t}={n}" for t, n in counts.items()))
PYEOF

if [ "$YES" -ne 1 ]; then
    read -r -p "Replace $DB with this backup? The site keeps serving but submissions pause. [y/N] " ans
    case "$ans" in y|Y|yes) ;; *) echo "restore: aborted"; exit 1 ;; esac
fi

echo "restore: stopping api and worker"
compose stop worker api

SAFE="$DATA_DIR/pre-restore-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$SAFE"
for f in "$DB" "$DB-wal" "$DB-shm" "$DB-journal"; do
    if [ -e "$f" ]; then mv "$f" "$SAFE/"; fi
done
echo "restore: previous database moved to $SAFE"

install -o 10001 -g 10001 -m 0640 "$WORK/platform.db" "$DB"
chown 10001:10001 "$SAFE"

echo "restore: starting api and worker"
compose up -d api worker
echo "restore: done. Check: curl -fsS https://$(env_get DOMAIN)/api/contest"
