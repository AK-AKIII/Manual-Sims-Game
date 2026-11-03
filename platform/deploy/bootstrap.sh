#!/bin/bash
# Poker Run: first-boot setup for an EC2 instance running UBUNTU 24.04 LTS (x86_64 or arm64).
#
# Use: paste this whole file as the instance's "User data" (Advanced details), after
# editing the SETTINGS block. It runs once as root at first boot; log:
#   /var/log/pokerrun-bootstrap.log
# It copies itself to /usr/local/sbin/pokerrun-bootstrap, so you can re-run it later:
#   sudo pokerrun-bootstrap                         (idempotent; never overwrites .env)
#   sudo REPO_URL=git@github.com:AK-AKIII/Manual-Sims-Game.git pokerrun-bootstrap
#
# What it does: OS updates + unattended upgrades, Docker Engine + compose plugin
# (Docker's apt repo), AWS CLI v2, git clone of the repo into $APP_DIR, host dirs
# under $POKERRUN_ROOT, platform/deploy/.env with generated secrets, the judge
# image, `docker compose up -d --build`, and the backup/cleanup cron jobs.
#
# Private repository? Two options (RUNBOOK.md, "Private repository"):
#   a) Deploy key (recommended): set REPO_URL to the git@github.com:... form. The
#      first run creates a key and stops, printing the public key (also saved to
#      /home/ubuntu/pokerrun_deploy_key.pub). Add it on GitHub (repo -> Settings ->
#      Deploy keys, read-only), then run: sudo pokerrun-bootstrap
#   b) Fine-grained token in the URL: https://x-access-token:TOKEN@github.com/...
#      Simple, but the token is visible in the instance's user data and in .git/config.

# ------------------------------------------------------------------ SETTINGS
REPO_URL="${REPO_URL:-https://github.com/AK-AKIII/Manual-Sims-Game.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
DOMAIN="${DOMAIN:-}"                    # e.g. pokerrun.example.com (needed to start the stack)
BACKUP_BUCKET="${BACKUP_BUCKET:-}"      # e.g. pokerrun-backups-123456789012
FRAME_ANCESTORS="${FRAME_ANCESTORS:-}"  # e.g. "https://yourname.com https://www.yourname.com"
ADMINS="${ADMINS:-admin}"               # PR_ADMINS: usernames you will register yourself
START_STACK="${START_STACK:-1}"         # 0 = install everything but do not start
APP_USER="${APP_USER:-ubuntu}"          # login user that owns the checkout
APP_DIR="${APP_DIR:-/opt/pokerrun}"
POKERRUN_ROOT="${POKERRUN_ROOT:-/srv/pokerrun}"
# ---------------------------------------------------------------------------

set -euo pipefail
exec > >(tee -a /var/log/pokerrun-bootstrap.log) 2>&1
echo "=== pokerrun bootstrap $(date -u +%FT%TZ) ==="
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

SELF="$(readlink -f "$0" 2>/dev/null || echo "$0")"
if [ -f "$SELF" ] && [ "$SELF" != /usr/local/sbin/pokerrun-bootstrap ]; then
    install -m 0700 "$SELF" /usr/local/sbin/pokerrun-bootstrap
fi

. /etc/os-release
[ "${ID:-}" = ubuntu ] || echo "WARNING: written for Ubuntu 24.04, this is ${PRETTY_NAME:-unknown}"

# ---- packages, automatic security updates
apt-get update -y
apt-get -y -o Dpkg::Options::=--force-confold upgrade
apt-get install -y ca-certificates curl git unzip sqlite3 python3 make cron unattended-upgrades openssh-client
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

# ---- Docker Engine + compose plugin (https://docs.docker.com/engine/install/ubuntu/)
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
mkdir -p /etc/docker
if [ ! -f /etc/docker/daemon.json ]; then
    cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "5" },
  "live-restore": true
}
EOF
fi
systemctl enable docker
systemctl restart docker
id "$APP_USER" >/dev/null 2>&1 && usermod -aG docker "$APP_USER"

# ---- AWS CLI v2 (backups use the instance role)
if ! command -v aws >/dev/null; then
    tmp="$(mktemp -d)"
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o "$tmp/awscliv2.zip"
    unzip -q "$tmp/awscliv2.zip" -d "$tmp"
    "$tmp/aws/install" --update
    rm -rf "$tmp"
fi

# ---- swap (helps image builds on 2-4 GB instances; sandboxes get no swap: --memory-swap = --memory)
if ! swapon --show | grep -q . && [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# ---- clone or update the repository
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
as_app() { sudo -u "$APP_USER" -H "$@"; }
mkdir -p "$APP_DIR"
chown "$APP_USER:$APP_USER" "$APP_DIR"
case "$REPO_URL" in
    git@*|ssh://*)
        KEY="$APP_HOME/.ssh/pokerrun_deploy"
        as_app mkdir -p "$APP_HOME/.ssh"
        chmod 700 "$APP_HOME/.ssh"
        if [ ! -f "$KEY" ]; then
            as_app ssh-keygen -t ed25519 -N '' -C "pokerrun-deploy@$(hostname)" -f "$KEY"
        fi
        cp "$KEY.pub" "$APP_HOME/pokerrun_deploy_key.pub"
        chown "$APP_USER:$APP_USER" "$APP_HOME/pokerrun_deploy_key.pub"
        export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
        if ! as_app env GIT_SSH_COMMAND="$GIT_SSH_COMMAND" git ls-remote "$REPO_URL" >/dev/null 2>&1; then
            echo "=================================================================="
            echo "Add this deploy key (read-only) on GitHub: repo -> Settings -> Deploy keys"
            cat "$KEY.pub"
            echo "Then run: sudo pokerrun-bootstrap"
            echo "=================================================================="
            exit 0
        fi
        ;;
    *) unset GIT_SSH_COMMAND || true ;;
esac
GITENV=()
[ -n "${GIT_SSH_COMMAND:-}" ] && GITENV=("GIT_SSH_COMMAND=$GIT_SSH_COMMAND")
if [ -d "$APP_DIR/.git" ]; then
    as_app env "${GITENV[@]}" git -C "$APP_DIR" pull --ff-only
else
    as_app env "${GITENV[@]}" git clone --branch "$REPO_BRANCH" "$REPO_URL" "$APP_DIR"
    if [ -n "${GIT_SSH_COMMAND:-}" ]; then
        as_app git -C "$APP_DIR" config core.sshCommand "$GIT_SSH_COMMAND"
    fi
fi
DEPLOY="$APP_DIR/platform/deploy"
chmod +x "$DEPLOY"/*.sh

# ---- host directories (uid 10001 = the app user inside the containers)
mkdir -p "$POKERRUN_ROOT/data" "$POKERRUN_ROOT/jobs" "$POKERRUN_ROOT/backups"
chown 10001:10001 "$POKERRUN_ROOT/data" "$POKERRUN_ROOT/jobs" "$POKERRUN_ROOT/backups"
chmod 0750 "$POKERRUN_ROOT/data" "$POKERRUN_ROOT/jobs"
chmod 0755 "$POKERRUN_ROOT/backups"

# ---- .env (created once, never overwritten)
ENV_FILE="$DEPLOY/.env"
if [ ! -f "$ENV_FILE" ]; then
    cp "$DEPLOY/env.example" "$ENV_FILE"
    DOCKER_GID="$(getent group docker | cut -d: -f3)" \
    E_DOMAIN="$DOMAIN" E_BUCKET="$BACKUP_BUCKET" E_FRAME="$FRAME_ANCESTORS" E_ADMINS="$ADMINS" \
    E_ROOT="$POKERRUN_ROOT" python3 - "$ENV_FILE" <<'PYEOF'
import os, re, secrets, sys
path = sys.argv[1]
r = secrets.SystemRandom()
values = {
    "PR_SECRET": secrets.token_hex(32),
    "PR_INVITE": secrets.token_urlsafe(9),
    "PR_HIDDEN_SEEDS": ",".join(map(str, sorted(r.sample(range(10**6, 4 * 10**9), 50)))),
    "DOCKER_GID": os.environ["DOCKER_GID"],
    "POKERRUN_ROOT": os.environ["E_ROOT"],
    "PR_ADMINS": os.environ["E_ADMINS"],
}
if os.environ["E_DOMAIN"]: values["DOMAIN"] = os.environ["E_DOMAIN"]
if os.environ["E_BUCKET"]: values["BACKUP_BUCKET"] = os.environ["E_BUCKET"]
if os.environ["E_FRAME"]: values["FRAME_ANCESTORS"] = '"%s"' % os.environ["E_FRAME"]
text = open(path).read()
for k, v in values.items():
    text, n = re.subn(rf"(?m)^{k}=.*$", lambda m: f"{k}={v}", text)
    if not n:
        text += f"\n{k}={v}\n"
open(path, "w").write(text)
PYEOF
    chown "$APP_USER:$APP_USER" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "Created $ENV_FILE with a new PR_SECRET, PR_INVITE and 50 hidden seeds. Copy it to a password manager."
fi
env_get() { grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'; }

# ---- judge sandbox image (lives in the host daemon; the worker runs it)
JUDGE_IMAGE="$(env_get PR_JUDGE_IMAGE)"; JUDGE_IMAGE="${JUDGE_IMAGE:-poker-run-judge}"
docker build -t "$JUDGE_IMAGE" "$APP_DIR/platform/judge"

# ---- cron: backups hourly, stale job dirs every 30 min; logrotate for the backup log
cat > /etc/cron.d/pokerrun <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
17 * * * * root $DEPLOY/backup.sh >> /var/log/pokerrun-backup.log 2>&1
*/30 * * * * root find $POKERRUN_ROOT/jobs -mindepth 1 -maxdepth 1 -mmin +120 -exec rm -rf {} +
EOF
chmod 0644 /etc/cron.d/pokerrun
cat > /etc/logrotate.d/pokerrun <<'EOF'
/var/log/pokerrun-backup.log /var/log/pokerrun-bootstrap.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
}
EOF
systemctl enable --now cron

# ---- start
if [ "$START_STACK" = 1 ] && [ -n "$(env_get DOMAIN)" ] && [ "$(env_get DOMAIN)" != pokerrun.example.com ]; then
    docker compose --project-directory "$DEPLOY" -f "$DEPLOY/docker-compose.yml" up -d --build
    echo "Stack started for https://$(env_get DOMAIN)/ (first certificate can take a minute once DNS points here)."
else
    echo "Not starting: set DOMAIN in $ENV_FILE, then: cd $DEPLOY && make up"
fi
echo "=== pokerrun bootstrap finished $(date -u +%FT%TZ) ==="
