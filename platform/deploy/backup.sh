#!/usr/bin/env bash
# Online backup of the Poker Run SQLite database, then upload to S3.
#
#   sudo platform/deploy/backup.sh            # normally run by cron (/etc/cron.d/pokerrun)
#
# 1. Inside the api container (as the app user, so no file ever gets the wrong
#    owner) Python's sqlite3 backup API copies the live database to a consistent
#    snapshot while the site keeps running, checks it with PRAGMA integrity_check
#    and gzips it to ${POKERRUN_ROOT}/backups/platform-YYYYmmddTHHMMSSZ.db.gz.
# 2. The host's AWS CLI uploads it to s3://$BACKUP_BUCKET/$BACKUP_PREFIX using the
#    EC2 instance role (no keys on disk).
# 3. Local copies older than BACKUP_KEEP_DAYS are deleted. S3 retention is a
#    bucket lifecycle rule, so the instance role needs no delete permission.
#
# Settings come from platform/deploy/.env: BACKUP_BUCKET, BACKUP_PREFIX,
# BACKUP_KEEP_DAYS, AWS_REGION, POKERRUN_ROOT.
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$DEPLOY_DIR/.env"
[ -f "$ENV_FILE" ] || { echo "backup: $ENV_FILE not found" >&2; exit 1; }

# Read KEY from .env without executing it (strips surrounding quotes).
env_get() {
    local line
    line="$(grep -E "^[[:space:]]*$1=" "$ENV_FILE" | tail -n 1 || true)"
    line="${line#*=}"
    line="${line%$'\r'}"
    line="${line#\"}"; line="${line%\"}"
    line="${line#\'}"; line="${line%\'}"
    printf '%s' "$line"
}

BUCKET="$(env_get BACKUP_BUCKET)"
PREFIX="$(env_get BACKUP_PREFIX)"; PREFIX="${PREFIX:-pokerrun/db/}"
case "$PREFIX" in */) ;; *) PREFIX="$PREFIX/" ;; esac
KEEP_DAYS="$(env_get BACKUP_KEEP_DAYS)"; KEEP_DAYS="${KEEP_DAYS:-7}"
ROOT="$(env_get POKERRUN_ROOT)"; ROOT="${ROOT:-/srv/pokerrun}"
REGION="$(env_get AWS_REGION)"
BACKUP_DIR="$ROOT/backups"

compose() { docker compose --project-directory "$DEPLOY_DIR" -f "$DEPLOY_DIR/docker-compose.yml" "$@"; }

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="platform-$STAMP.db.gz"
echo "backup: $(date -u +%FT%TZ) creating $BACKUP_DIR/$NAME"

# Python run inside the app image: online backup + integrity check + gzip.
read -r -d '' PY <<'PYEOF' || true
import gzip, os, shutil, sqlite3, sys
name = sys.argv[1]
db = os.environ.get("PR_DB", "/data/platform.db")
if not os.path.exists(db):
    sys.exit(f"database {db} does not exist yet")
tmp = f"/backups/.{name}.db"
src = sqlite3.connect(db, timeout=30)
dst = sqlite3.connect(tmp)
with dst:
    src.backup(dst)          # consistent snapshot, safe while api/worker write
src.close()
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close()
if ok != "ok":
    os.remove(tmp)
    sys.exit(f"integrity_check failed: {ok}")
with open(tmp, "rb") as f, gzip.open(f"/backups/.{name}", "wb", compresslevel=6) as g:
    shutil.copyfileobj(f, g)
os.remove(tmp)
os.replace(f"/backups/.{name}", f"/backups/{name}")
print(f"backup: wrote /backups/{name} ({os.path.getsize('/backups/' + name)} bytes)")
PYEOF

if compose ps --status running --services 2>/dev/null | grep -qx api; then
    compose exec -T api python -c "$PY" "$NAME"
else
    # api not running (e.g. stopped for maintenance): use a one-off container.
    compose run --rm --no-deps -T api python -c "$PY" "$NAME"
fi

if [ -n "$BUCKET" ]; then
    if [ -z "$REGION" ]; then
        # IMDSv2: the instance's own region.
        TOKEN="$(curl -fsS -m 5 -X PUT http://169.254.169.254/latest/api/token \
                 -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' || true)"
        if [ -n "$TOKEN" ]; then
            REGION="$(curl -fsS -m 5 -H "X-aws-ec2-metadata-token: $TOKEN" \
                      http://169.254.169.254/latest/meta-data/placement/region || true)"
        fi
    fi
    REGION_ARGS=()
    [ -n "$REGION" ] && REGION_ARGS=(--region "$REGION")
    aws s3 cp "$BACKUP_DIR/$NAME" "s3://$BUCKET/$PREFIX$NAME" \
        "${REGION_ARGS[@]}" --only-show-errors --sse AES256
    echo "backup: uploaded s3://$BUCKET/$PREFIX$NAME"
else
    echo "backup: BACKUP_BUCKET is empty, keeping the local copy only" >&2
fi

# Local retention.
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'platform-*.db.gz' -mtime +"$KEEP_DAYS" -print -delete
echo "backup: done"
