# Poker Run on AWS: runbook

This guide takes you from "I have an AWS account" to a running contest site at
`https://pokerrun.example.com`, and covers running the contest, backups, updates
and teardown. It assumes little AWS experience. Replace these placeholders everywhere:

| Placeholder | Meaning | Example |
| --- | --- | --- |
| `pokerrun.example.com` | the contest host name (a subdomain of a domain you control) | `pokerrun.yourname.com` |
| `EIP` | the instance's Elastic IP address | `13.234.10.20` |
| `BUCKET` | the S3 bucket for backups | `pokerrun-backups-123456789012` |
| `ADMIN` | your admin username on the site | `admin` |

Contents: [Architecture](#architecture) ·
[0. Before you start](#0-before-you-start) · [1. Region](#1-choose-a-region) ·
[2. Instance type](#2-choose-an-instance-type) · [3. S3 bucket](#3-s3-bucket-for-backups) ·
[4. IAM role](#4-iam-role-for-the-instance) · [5. Elastic IP + DNS](#5-elastic-ip-and-dns) ·
[6. Launch](#6-launch-the-instance) · [7. First login](#7-first-login-and-checks) ·
[8. Secrets and settings](#8-secrets-and-contest-settings) · [9. Admin user](#9-create-the-admin-user) ·
[10. Smoke tests](#10-smoke-tests) · [Operations](#operations) ·
[Contest day](#contest-day-checklist) · [Backups](#backups-and-the-restore-drill) ·
[Cost](#cost-estimate) · [Teardown](#tearing-down) · [Security notes](#security-notes) ·
[Troubleshooting](#troubleshooting)

## Architecture

One EC2 instance (Ubuntu 24.04) runs Docker with three long-lived containers
(`platform/deploy/docker-compose.yml`) plus one short-lived sandbox container per
submission:

```
 internet ──443/80──▶ caddy ──/api/*──▶ api (uvicorn prplatform.app:app, :8000, not published)
                       │                  │
                       └─ static site     └─ SQLite  /srv/pokerrun/data/platform.db  ◀─ worker
                          (baked into                                                  (python -m prplatform.worker)
                           the caddy image)                                                │ docker.sock
                                                                                           ▼
                                                     host Docker daemon ──▶ poker-run-judge sandbox
                                                     (--network none, read-only, uid 65534, 1 CPU, 1 GB)
                                                     mounts /srv/pokerrun/jobs/pr-job-XXXX at /job:ro
```

- **caddy** (`Dockerfile.site`): gets the HTTPS certificate itself, serves the static
  site built by `platform/site/build.py` (landing, `/problem/`, `/play/`, `/contest/`,
  `/media/`, `/downloads/`), proxies `/api/*` to the api. Config: `Caddyfile`.
- **api** and **worker** (`Dockerfile.app`, same image): FastAPI app and judge
  worker, user 10001, read-only root filesystem, no Linux capabilities.
- **worker** talks to the **host's** Docker daemon through the mounted
  `/var/run/docker.sock` to start one sandbox per submission. Job directories live in
  `/srv/pokerrun/jobs`, which is mounted at the same path inside the worker, so the
  path the worker hands to `docker run -v {dir}:/job:ro` exists on the host. The
  worker never runs submitted code itself (see [Security notes](#security-notes)).
- **Host directories**: `/opt/pokerrun` (the git checkout), `/srv/pokerrun/data`
  (database), `/srv/pokerrun/jobs` (temporary), `/srv/pokerrun/backups` (local
  backups, exports). Configuration: `/opt/pokerrun/platform/deploy/.env`.
- **Backups**: cron runs `backup.sh` hourly: an online SQLite backup, uploaded to S3
  with the instance's IAM role.

Files in `platform/deploy/`:

| File | Purpose |
| --- | --- |
| `docker-compose.yml` | the three services, volumes, healthcheck, log rotation |
| `Dockerfile.app` (+ `.dockerignore`) | api/worker image (python:3.12-slim, docker CLI, poker_run, platform/server) |
| `Dockerfile.site` (+ `.dockerignore`) | runs `platform/site/build.py`, bakes `dist/` into a Caddy image |
| `Caddyfile` | HTTPS, proxy, caching, security headers, CSP, body limit |
| `env.example` | every setting, documented; copied to `.env` |
| `bootstrap.sh` | EC2 user data: installs and starts everything |
| `backup.sh`, `restore.sh` | database backup to S3 and restore |
| `Makefile` | `make help` lists the everyday commands |
| `aws/*.json` | IAM policy, EC2 trust policy, S3 lifecycle rule |

---

## 0. Before you start

1. **Push the code.** The server clones the repository, so everything must be
   committed and pushed to `main`: `poker_run/`, `platform/` (server, judge, site,
   deploy) and the explainer video `video/poker_run_explainer.mp4` (18 MB, fine for
   GitHub; add `video/poker_run_explainer.webm` too if you have it). Without the
   video the site still works but `/media/poker_run_explainer.mp4` is a 404. Check:
   `git ls-files platform/deploy video/poker_run_explainer.mp4`.
2. **A domain** you can add DNS records to (Route 53, Vercel, Cloudflare, your registrar...).
   You will create one subdomain, for example `pokerrun.yourname.com`.
3. **Account hygiene** (once per AWS account, 10 minutes, strongly recommended):
   - Turn on MFA for the root user (IAM console → "Add MFA"), and do daily work as an
     IAM user or IAM Identity Center user with MFA, not as root.
   - Billing → Budgets → Create budget → "Monthly cost budget", e.g. $100, with an
     email alert at 80 %. This is your safety net against a forgotten instance.
4. **Your public IP** for SSH: search "what is my ip". If it changes (home internet,
   travel), you will update the security group rule later.

## 1. Choose a region

Pick the AWS region closest to most contestants (lower latency for the site; judging
speed does not depend on it). Examples: India → `ap-south-1` (Mumbai); Europe →
`eu-west-1` (Ireland) or `eu-central-1` (Frankfurt); US → `us-east-1` (N. Virginia,
usually the cheapest). Select it in the top-right corner of the AWS console and use
**the same region for everything below** (the S3 bucket too).

## 2. Choose an instance type

How judging works: the worker runs submissions one at a time per judging thread
(`PR_WORKERS`, default 1). One **full** submission runs every hidden seed with a
2-second limit each: up to `hidden_count × 2 s` = 50 × 2 = **100 s**, plus about a
second of container start and the checker. A **public** submission is 10 × 2 = 20 s.
Heuristic solvers usually use most of their time budget, so plan with the worst case:

- one judging thread ≈ **30–35 full submissions per hour**;
- each student may send `PR_FULL_PER_HOUR` = 6 full submissions per hour, but in
  practice a few per hour, peaking before the deadline;
- the 2 s limit is **wall-clock**: if two sandboxes share one physical CPU core,
  both solvers get slower and results become noisy. So keep `PR_WORKERS` at or below
  the number of **physical cores**. On most Intel/AMD instances (t3, c7i, m7i...)
  2 vCPUs = 1 core; on Graviton (c7g, t4g) and AMD c7a/m7a, 1 vCPU = 1 core.
- memory: each running sandbox may use `PR_MEMORY_MB` = 1 GB; keep
  `PR_WORKERS × 1 GB + 1.5 GB` under the instance's RAM.

| Instance | vCPU / RAM | Cores | PR_WORKERS | Full submissions/hour | Use |
| --- | --- | --- | --- | --- | --- |
| `t3.medium` | 2 / 4 GiB | 1 (burstable) | 1 | ~30 | setup, testing, quiet weeks. Burstable: a sustained judge queue spends CPU credits (billed extra in "unlimited" mode), so not for contest day |
| `c7i.large` | 2 / 4 GiB | 1 | 1 | ~30 | **recommended default**: steady full-speed CPU, up to ~20 active contestants |
| `c7i.xlarge` | 4 / 8 GiB | 2 | 2 | ~60 | contest day with 20–60 active contestants |
| `c7i.2xlarge` | 8 / 16 GiB | 4 | 3–4 | ~100–130 | big contests |
| `c7g.large` / `c7g.xlarge` (ARM) | 2 / 4, 4 / 8 GiB | 2 / 4 | 2 / 3 | ~60 / ~90 | cheaper per core; everything here runs on arm64 too (choose the arm64 Ubuntu AMI) |

You can change the type later in two minutes (see [Scaling the judge](#scaling-the-judge)),
so start with `t3.medium` or `c7i.large` and resize a few days before the contest.
Tell contestants that the time limit is measured on the judge, which may be slower
than their laptop.

## 3. S3 bucket for backups

Console: **S3 → Create bucket**.

1. Bucket type: General purpose. Name: globally unique, e.g. `pokerrun-backups-<your 12-digit account id>`.
   Region: the one from step 1.
2. Object Ownership: ACLs disabled (default). **Block all public access: on** (default).
3. **Bucket Versioning: Enable.** (Overwrites and deletions keep the old version, so a
   mistake or a compromised server cannot destroy old backups.)
4. Default encryption: SSE-S3 (default). Create.
5. Open the bucket → **Management → Create lifecycle rule**: name `expire-pokerrun-db-backups`;
   prefix `pokerrun/db/`; actions: "Expire current versions" after **30** days,
   "Permanently delete noncurrent versions" after **7** days, "Delete expired object
   delete markers or incomplete multipart uploads" (incomplete uploads: 1 day). Save.

Equivalent AWS CLI (from any machine with admin credentials, or CloudShell, the `>_` icon in the console):

```sh
B=pokerrun-backups-123456789012; R=ap-south-1
aws s3api create-bucket --bucket $B --region $R --create-bucket-configuration LocationConstraint=$R   # omit the last option in us-east-1
aws s3api put-public-access-block --bucket $B --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-versioning --bucket $B --versioning-configuration Status=Enabled
aws s3api put-bucket-lifecycle-configuration --bucket $B --lifecycle-configuration file://platform/deploy/aws/s3-lifecycle.json
```

## 4. IAM role for the instance

The server uploads backups with an **instance role**: temporary credentials that AWS
rotates automatically, so no access keys are ever stored on the server. The policy
allows only reading and writing objects under `pokerrun/db/` in your bucket, and
listing that prefix. It has **no delete permission**: retention is the lifecycle rule.

Console: **IAM → Policies → Create policy → JSON**, paste
`platform/deploy/aws/iam-backup-policy.json` with `BUCKET_NAME` replaced by your bucket
name (two places). Next → name `PokerRunBackups` → Create.

**IAM → Roles → Create role** → Trusted entity: AWS service, Use case: **EC2** → Next →
tick `PokerRunBackups` → (optional, recommended) also tick
`AmazonSSMManagedInstanceCore`, which lets you open a shell from the browser with
Session Manager instead of SSH → Next → Role name `PokerRunInstance` → Create role.
(The console creates the matching instance profile automatically.)

CLI equivalent:

```sh
sed "s/BUCKET_NAME/$B/g" platform/deploy/aws/iam-backup-policy.json > /tmp/policy.json
aws iam create-policy --policy-name PokerRunBackups --policy-document file:///tmp/policy.json
aws iam create-role --role-name PokerRunInstance --assume-role-policy-document file://platform/deploy/aws/ec2-trust-policy.json
aws iam attach-role-policy --role-name PokerRunInstance --policy-arn arn:aws:iam::<ACCOUNT_ID>:policy/PokerRunBackups
aws iam create-instance-profile --instance-profile-name PokerRunInstance
aws iam add-role-to-instance-profile --instance-profile-name PokerRunInstance --role-name PokerRunInstance
```

## 5. Elastic IP and DNS

Do this **before** launching, so that the name already resolves when Caddy asks Let's
Encrypt for a certificate.

1. **EC2 → Elastic IPs → Allocate Elastic IP address** → Allocate. Note the address (`EIP`).
   An Elastic IP stays the same across stops, starts and instance type changes.
2. Create a DNS **A record**: name `pokerrun` (the subdomain), value `EIP`, TTL 300.
   - **Route 53** (domain's hosted zone in Route 53): Route 53 → Hosted zones → your
     domain → Create record → Record name `pokerrun`, Record type `A`, Value `EIP`,
     TTL `300`, Routing policy Simple → Create records.
   - **Vercel DNS** (if your Next.js site's domain uses Vercel nameservers): Vercel →
     Domains → your domain → DNS Records → Add: Name `pokerrun`, Type `A`, Value `EIP`.
   - **Elsewhere** (registrar, Cloudflare...): add the same A record. On Cloudflare set
     the proxy status to **DNS only** (grey cloud), otherwise Caddy cannot get its certificate.
   - Do not create an AAAA record for this name (the instance has no IPv6 by default).
   - If your domain has CAA records, one must allow `letsencrypt.org`.
3. Check from your computer (may take a few minutes): `dig +short pokerrun.example.com`
   (or `nslookup pokerrun.example.com`) prints `EIP`.

## 6. Launch the instance

First edit the SETTINGS block at the top of `platform/deploy/bootstrap.sh` in a text
editor (a local copy is fine; nothing secret goes in it):

```sh
REPO_URL="${REPO_URL:-https://github.com/AK-AKIII/Manual-Sims-Game.git}"   # see "Private repository" below
REPO_BRANCH="${REPO_BRANCH:-main}"   # use poker-run if you deploy before merging that branch into main
DOMAIN="${DOMAIN:-pokerrun.example.com}"
BACKUP_BUCKET="${BACKUP_BUCKET:-pokerrun-backups-123456789012}"
FRAME_ANCESTORS="${FRAME_ANCESTORS:-https://yourname.com https://www.yourname.com}"   # your Next.js site, for the iframe
ADMINS="${ADMINS:-admin}"
```

Secrets (`PR_SECRET`, the hidden seeds, the invite code) are generated **on the
server** and never appear in user data.

**EC2 → Instances → Launch instances**:

1. Name: `pokerrun`.
2. AMI: **Ubuntu Server 24.04 LTS**, 64-bit (x86) — or 64-bit (Arm) for c7g/t4g types.
3. Instance type: from step 2 (e.g. `c7i.large`).
4. Key pair: Create new key pair → name `pokerrun`, type ED25519, format `.pem` →
   it downloads `pokerrun.pem`. Keep it safe; on macOS/Linux run `chmod 400 pokerrun.pem`.
5. Network settings → Edit: default VPC, a public subnet, Auto-assign public IP:
   Enable. **Create security group** `pokerrun-web` with these inbound rules:

   | Type | Port | Source | Why |
   | --- | --- | --- | --- |
   | SSH | 22 | **My IP** (never 0.0.0.0/0) | your shell access |
   | HTTP | 80 | Anywhere-IPv4 (0.0.0.0/0), Anywhere-IPv6 (::/0) | Let's Encrypt challenge, redirect to HTTPS |
   | HTTPS | 443 | Anywhere-IPv4, Anywhere-IPv6 | the site |
   | Custom UDP | 443 | Anywhere-IPv4, Anywhere-IPv6 | HTTP/3 (optional) |

   Outbound: leave "all traffic" (needed for apt, Docker Hub, GitHub, Let's Encrypt, S3).
6. Storage: **30 GiB gp3** (Docker images, logs, backups).
7. Advanced details:
   - IAM instance profile: **`PokerRunInstance`**.
   - Metadata version: **V2 only (token required)**; Metadata response hop limit: **1**
     (this keeps the containers from reaching the instance role's credentials).
   - User data: paste the **whole edited `bootstrap.sh`** (it is under the 16 KB limit).
8. Launch instance.
9. Right away: **EC2 → Elastic IPs →** select your `EIP` → Actions → **Associate Elastic IP
   address** → Instance `pokerrun` → Associate. From now on connect via `EIP`.

The bootstrap takes about 5–10 minutes (OS updates, Docker, AWS CLI, git clone, image
builds, start). Meanwhile the site is not reachable yet.

### Private repository

If the GitHub repository is private, the server needs read access. Pick one:

- **Deploy key (recommended).** Set `REPO_URL="git@github.com:AK-AKIII/Manual-Sims-Game.git"`
  in the SETTINGS. The first boot installs everything, creates an SSH key and stops
  before cloning. SSH in (step 7), run `cat ~/pokerrun_deploy_key.pub`, and add it on
  GitHub: repository → Settings → Deploy keys → Add deploy key, title `pokerrun-ec2`,
  paste, leave "Allow write access" **unticked**. Then run `sudo pokerrun-bootstrap`
  to finish. Later `git pull`s use the key automatically (`core.sshCommand` is set in the checkout).
- **Fine-grained personal access token.** GitHub → Settings → Developer settings →
  Fine-grained tokens → only this repository, permission Contents: Read-only, an
  expiry after the contest. Use
  `REPO_URL="https://x-access-token:<TOKEN>@github.com/AK-AKIII/Manual-Sims-Game.git"`.
  Simpler, but the token is readable by anyone who can view the instance's user data
  in your AWS account, and it is stored in `/opt/pokerrun/.git/config`.

A public repository needs nothing: the default HTTPS URL works.

## 7. First login and checks

```sh
ssh -i pokerrun.pem ubuntu@EIP
cloud-init status --wait                      # waits until the bootstrap is done; "status: done"
sudo tail -n 50 /var/log/pokerrun-bootstrap.log
cd /opt/pokerrun/platform/deploy
make ps                                       # caddy, api (healthy), worker: all "Up"
```

(Log out and in once if `docker` says "permission denied": the `ubuntu` user was added
to the `docker` group during the bootstrap.)

With Session Manager instead of SSH: EC2 → the instance → Connect → Session Manager →
Connect, then `sudo -iu ubuntu`.

If the bootstrap printed "Not starting: set DOMAIN", or you want to change anything
first, continue with step 8 and finish with `make up`.

## 8. Secrets and contest settings

All configuration is in `/opt/pokerrun/platform/deploy/.env` (mode 600, owned by
`ubuntu`). The bootstrap created it from `env.example` and filled in:

- `PR_SECRET`: 64 random hex chars (reserved by API.md; the current server uses random session tokens and does not need it).
- `PR_HIDDEN_SEEDS`: 50 random seeds (the secret tests).
- `PR_INVITE`: a random invite code.
- `DOMAIN`, `BACKUP_BUCKET`, `FRAME_ANCESTORS`, `PR_ADMINS`, `DOCKER_GID` from the SETTINGS.

Edit it: `nano /opt/pokerrun/platform/deploy/.env`. Things to set now:

| Setting | What to put |
| --- | --- |
| `PR_CONTEST_NAME` | e.g. `"Poker Run 2026"` (quotes because of the space) |
| `PR_INVITE` | the code you will give students, or empty for open registration |
| `PR_START`, `PR_END` | e.g. `2026-12-05T09:00:00Z`, `2026-12-06T09:00:00Z` (UTC) |
| `PR_FREEZE` | e.g. one hour before `PR_END`; empty = no freeze |
| `PR_WORKERS` | judging threads, see step 2 |

Generate fresh values by hand if you prefer (one-liners, also at the top of `env.example`):

```sh
python3 -c "import secrets; print(secrets.token_hex(32))"                       # PR_SECRET
python3 -c "import secrets; r=secrets.SystemRandom(); print(','.join(map(str, sorted(r.sample(range(10**6, 4*10**9), 50)))))"   # PR_HIDDEN_SEEDS
python3 -c "import secrets; print(secrets.token_urlsafe(9))"                    # PR_INVITE
```

Rules: no `$` in values, no spaces around `=`, comments on their own lines. **Do not
change `PR_HIDDEN_SEEDS` once full submissions exist** (scores compare runs on the same
seeds).

**Save a copy of `.env` in your password manager now** (at least
`PR_HIDDEN_SEEDS` and `PR_INVITE`). Database backups do not contain it, and without the hidden seeds
nobody can re-judge or verify the final results.

Apply: `make up` (refuses to start while a value is still `CHANGE_ME`). Compose
recreates only the containers whose configuration changed.

## 9. Create the admin user

Admins are the usernames in `PR_ADMINS`. Claim yours before announcing the site:

1. Open `https://pokerrun.example.com/contest/` and register with username `ADMIN`
   (exactly as in `PR_ADMINS`), a strong password and the invite code.
2. Check: in the browser console on that page,
   `fetch('/api/me').then(r => r.json()).then(console.log)` prints `is_admin: true`.
   If you registered before adding the name to `PR_ADMINS`, use `set-admin` below.

Alternatives on the server:

```sh
cd /opt/pokerrun/platform/deploy
docker compose exec api python -m prplatform.manage list-users
docker compose exec api python -m prplatform.manage set-admin alice        # --off to remove
docker compose exec api python -m prplatform.manage set-password alice     # prompts for the new password; logs out their sessions
```

Changing `PR_ADMINS` needs `make up` (restarts api and worker).

## 10. Smoke tests

From your computer or the server:

```sh
D=https://pokerrun.example.com
curl -fsS $D/api/contest | python3 -m json.tool       # name, open, hidden_count 50, time_limit 2.0 ...
curl -sI $D/ | grep -iE '^(HTTP|strict-transport|content-security)'
curl -sI http://pokerrun.example.com/ | head -1         # 308 redirect to https
```

On the server, `make smoke` checks the landing page, `/play/`, the video and the leaderboard.

In the browser: the landing page plays the video; `/play/` deals a game; `/problem/`
downloads `starter.py`; `/contest/leaderboard/` loads.

**A public submission with the starter** (run on the server, where the starter is at hand):

```sh
D=https://pokerrun.example.com; H='X-Requested-With: fetch'; CT='Content-Type: application/json'
INVITE=$(grep '^PR_INVITE=' /opt/pokerrun/platform/deploy/.env | cut -d= -f2-)
curl -fsS -c /tmp/jar -H "$H" -H "$CT" \
  -d "{\"username\":\"smoketest\",\"password\":\"smoke-test-password-1\",\"invite\":\"$INVITE\"}" $D/api/register
python3 -c "import json; print(json.dumps({'kind': 'public', 'source': open('/opt/pokerrun/poker_run/starters/starter.py').read()}))" > /tmp/sub.json
curl -fsS -b /tmp/jar -H "$H" -H "$CT" --data-binary @/tmp/sub.json $D/api/submissions     # {"id": 1}
sleep 30; curl -fsS -b /tmp/jar $D/api/submissions | python3 -m json.tool                # status "done", tests_ac 10
```

(If `smoketest` already exists, use `/api/login` with `{"username","password"}` instead
of `/api/register`.) Watch it being judged: `make logs S=worker` and, in another
shell, `docker ps` shows a `pr-judge-...` container for the duration. Then submit the
starter once as `full` through the web page with your admin account to check the
hidden-seed path (admins may submit outside the contest window).

**Sandbox check (required before you invite anyone).** The Docker sandbox could not be
tested before deployment, so this is its first real run. Submit this as a *public*
submission. If the sandbox works, every test is `WA` (it prints no moves) and none is
`RE`. A test showing `RE` with one of the messages below means the sandbox is not
isolated: stop and fix it before the contest.

- `NETWORK OPEN`: the container can reach the internet.
- `WROTE ...`: the job folder or the image is writable.
- `SEES PR_ ENV`: the server's settings (including hidden seeds) leak into the sandbox.
- `LEFTOVER FILE ...` (on tests 2–10): something from the previous test survived. That
  lets a solver keep computing in the background and pool its time limit across tests.

```python
import os, socket, sys, time
try:
    socket.create_connection(("1.1.1.1", 80), timeout=2); sys.exit("NETWORK OPEN")
except OSError:
    pass
for path in ("/job/x", "/etc/x"):
    try:
        open(path, "w"); sys.exit("WROTE " + path)
    except OSError:
        pass
if any(k.startswith("PR_") for k in os.environ):
    sys.exit("SEES PR_ ENV")
for path in ("/tmp/pr_probe", "/dev/shm/pr_probe"):
    if os.path.exists(path):
        sys.exit("LEFTOVER FILE " + path)   # left by the previous test's background process
if os.fork() == 0:            # escape the process group, then keep running and write files
    os.setsid()
    if os.fork() == 0:
        for path in ("/tmp/pr_probe", "/dev/shm/pr_probe"):
            try:
                open(path, "w").write("x")
            except OSError:
                pass
        time.sleep(60)
    os._exit(0)
time.sleep(0.3)
```

---

# Operations

All commands run on the server in `/opt/pokerrun/platform/deploy` (or with
`make -C /opt/pokerrun/platform/deploy ...`). `make help` lists them.

### Updating

```sh
make update        # git pull --ff-only, rebuild the judge image, rebuild + restart the stack
```

`make rebuild` does the same without `git pull` (and refreshes base images: run it
monthly for security fixes). The api restarts in seconds (Caddy retries for 5 s, so
users rarely notice). The worker first finishes the submission it is judging (up to
about 3 minutes) and then restarts; queued submissions wait and are not lost. Avoid
updates during the contest unless something is broken.

Only the Caddyfile changed? `make reload` (no downtime). Only `.env` changed? `make up`.

### Viewing logs

```sh
make logs                       # everything, follow
make logs S=worker              # one service: api | worker | caddy
docker compose logs --since 2h api
sudo tail -f /var/log/pokerrun-backup.log
make sqlite                     # read-only Python prompt on the DB: q("SELECT status, COUNT(*) FROM submissions GROUP BY status")
```

Container logs rotate automatically (10 MB × 5 files per container). Caddy's access
log is JSON on stdout.

### Rejudging

After fixing a judge problem, put submissions back in the queue as an admin:

```sh
D=https://pokerrun.example.com; H='X-Requested-With: fetch'; CT='Content-Type: application/json'
curl -fsS -c /tmp/admin -H "$H" -H "$CT" -d '{"username":"ADMIN","password":"..."}' $D/api/login
curl -fsS -b /tmp/admin -H "$H" -X POST $D/api/admin/rejudge/123                 # one submission

# every submission that ended in 'error':
curl -fsS -b /tmp/admin $D/api/admin/submissions \
  | python3 -c "import json,sys; print(*[s['id'] for s in json.load(sys.stdin) if s['status']=='error'])" \
  | xargs -n1 -I{} curl -fsS -b /tmp/admin -H "$H" -X POST $D/api/admin/rejudge/{}
rm /tmp/admin
```

### Scaling the judge

There is **exactly one worker container** (on start it re-queues every `running`
submission, so a second worker would break the first one's runs). Parallelism comes
from judging threads inside it, `PR_WORKERS`:

```sh
make workers N=2               # writes PR_WORKERS=2 to .env and recreates the worker
```

Rules from [step 2](#2-choose-an-instance-type): `PR_WORKERS` ≤ physical cores, and
`PR_WORKERS × PR_MEMORY_MB + 1.5 GB` ≤ RAM. More threads on the same cores make every
solver slower (wall-clock limit), which is unfair; when the queue grows, prefer a
bigger instance:

1. Pick a quiet moment (ideally before the contest). EC2 → Instances → `pokerrun` →
   Instance state → **Stop**. Wait for "stopped".
2. Actions → Instance settings → **Change instance type** → e.g. `c7i.xlarge` → Apply.
3. Instance state → **Start**. The Elastic IP stays; Docker starts the stack by itself
   (`restart: unless-stopped`).
4. `make workers N=2`.

Downtime about 2 minutes. Queue length: `make sqlite`, then
`q("SELECT COUNT(*) FROM submissions WHERE status='queued'")`. If the queue keeps
growing on contest day you can also lower `PR_FULL_PER_HOUR` (then `make up`).

---

# Contest-day checklist

**A week before**
- [ ] Resize to the contest instance type, set `PR_WORKERS`.
- [ ] `.env`: `PR_START`, `PR_END`, `PR_FREEZE`, `PR_INVITE`, `PR_CONTEST_NAME`; `make up`.
      `curl $D/api/contest` shows the right times. Copy `.env` to your password manager.
- [ ] Admin account works; a `full` submission of the starter finishes with all tests AC.
- [ ] Restore drill (below) done; backups appear in S3 every hour.
- [ ] Your SSH rule still matches your IP (EC2 → Security groups → `pokerrun-web`).
- [ ] Announce the URL and invite code.

**The day before**
- [ ] Last `make update` (then freeze the code), `make smoke`, `df -h` (≥ 5 GB free).
- [ ] Optional: back up every 15 minutes during the contest:
      `sudo sed -i 's|^17 \* \* \* \*|*/15 * * * *|' /etc/cron.d/pokerrun`.

**During**
- [ ] Watch `make logs S=worker` and the queue length; scale as above if needed.
- [ ] No deploys unless something is broken.

**Freeze** happens by itself at `PR_FREEZE`: the public leaderboard stops updating;
admins still see it live.

**End** happens by itself at `PR_END`: full submissions are refused (public ones stay
allowed for logged-in users).

**After the end**
1. Wait until the queue is empty: `q("SELECT status, COUNT(*) FROM submissions GROUP BY status")`
   shows no `queued` or `running`.
2. `make backup` (final backup, also in S3).
3. Check the live standings as admin on `/contest/leaderboard/`.
4. Publish: set `PR_FREEZE=` (empty) in `.env`, then `make up`.
5. `make export`: writes `/srv/pokerrun/backups/exports/leaderboard-<time>.json` and
   `.csv` (the public leaderboard, now unfrozen). Download them:
   `scp -i pokerrun.pem 'ubuntu@EIP:/srv/pokerrun/backups/exports/*' .`
6. Keep the latest database backup with the results:
   `scp -i pokerrun.pem 'ubuntu@EIP:/srv/pokerrun/backups/platform-*.db.gz' .`
7. Reset the backup cron to hourly if you changed it.

---

# Backups and the restore drill

- **What**: the SQLite database (users, sessions, submissions with source code,
  results). Not included: `.env` (keep it in your password manager) and certificates
  (Caddy re-issues them).
- **When**: hourly at :17 by `/etc/cron.d/pokerrun`:
  `17 * * * * root /opt/pokerrun/platform/deploy/backup.sh >> /var/log/pokerrun-backup.log 2>&1`
  (plus a job that removes job directories older than 2 hours from `/srv/pokerrun/jobs`).
- **How**: `backup.sh` runs Python's `sqlite3` online backup API inside the api
  container (consistent while the site is running), checks the copy with
  `PRAGMA integrity_check`, gzips it to `/srv/pokerrun/backups/platform-<UTC time>.db.gz`
  and uploads it to `s3://BUCKET/pokerrun/db/`. Local copies are kept `BACKUP_KEEP_DAYS`
  (7) days; S3 keeps them 30 days (lifecycle rule) and old versions 7 more.
- **Now**: `make backup`. **List**: `aws s3 ls s3://BUCKET/pokerrun/db/`.

**Restore**:

```sh
make restore FROM=latest                                         # newest backup in S3
make restore FROM=pokerrun/db/platform-20261205T091700Z.db.gz    # a specific one
make restore FROM=/srv/pokerrun/backups/platform-20261205T091700Z.db.gz   # a local file
```

`restore.sh` downloads, unpacks and integrity-checks the backup, prints the row counts,
**asks for confirmation**, stops api and worker, moves the current database to
`/srv/pokerrun/data/pre-restore-<time>/` (nothing is deleted), puts the backup in
place and starts api and worker again. Submissions that were queued after the backup
are lost; ask those users to resubmit.

**Restore drill** (do it once before the contest, takes 2 minutes):
1. `make backup`, then `aws s3 ls s3://BUCKET/pokerrun/db/` shows the new file.
2. `make restore FROM=latest` and check the printed counts look right. Answer **N** to
   stop here (a non-destructive drill), or **y** for a full drill, after which the
   site must work as before (log in, see your submissions).

**Disaster recovery** (instance lost): launch a new instance as in steps 6–7 (same
Elastic IP, so DNS does not change), put your saved `.env` in
`/opt/pokerrun/platform/deploy/.env` (overwrite the generated one), `make up`, then
`make restore FROM=latest`.

---

# Cost estimate

On-demand prices, `us-east-1`, late 2025, per month (730 h); other regions are
similar (Mumbai is a bit cheaper for compute). Check the AWS pricing pages for exact
numbers in your region.

| Item | Price | Per month |
| --- | --- | --- |
| `t3.medium` / `c7i.large` / `c7i.xlarge` | $0.042 / $0.089 / $0.179 per hour | ~$30 / ~$65 / ~$130 |
| EBS gp3 30 GiB | $0.08 per GiB-month | $2.40 |
| Public IPv4 (the Elastic IP) | $0.005 per hour, attached or not | $3.65 |
| S3 backups (a few hundred MB with versions) | $0.023 per GB-month + requests | < $0.10 |
| Route 53 hosted zone (only if your DNS is in Route 53) | $0.50 per zone | $0.50 |
| Data transfer out | first 100 GB/month free, then ~$0.09/GB | $0 for most contests (the 18 MB video × 5,000 views ≈ 90 GB) |
| **Total** | | **~$37 (t3.medium), ~$72 (c7i.large)** |

Ways to pay less: run `t3.medium` until the week before the contest; resize to the
contest type for the contest days only (a weekend on `c7i.xlarge` costs about $9);
**stop** the instance when nobody needs it (you then pay only for the disk and the IP,
about $6/month); Graviton (`c7g`) costs about 15–20 % less per vCPU.

---

# Tearing down

1. Export results and download backups (contest-day checklist, steps 5–6).
2. EC2 → Instances → `pokerrun` → Instance state → **Terminate** (the disk is deleted with it).
3. EC2 → Elastic IPs → select → Actions → **Release Elastic IP address** (otherwise it keeps costing $3.65/month).
4. Delete the DNS A record `pokerrun`.
5. EC2 → Security groups → delete `pokerrun-web`; EC2 → Key pairs → delete `pokerrun`.
6. S3: keep the bucket for the record (cents per month) or empty it ("Empty" button,
   which also removes old versions) and delete it.
7. IAM: delete the role `PokerRunInstance` and the policy `PokerRunBackups`.
8. GitHub: delete the deploy key or token if you created one.
9. Check Billing → Bills next month shows nothing left running.

---

# Security notes

- **Sandbox.** Every submission runs in a fresh `poker-run-judge` container started
  with `--network none` (no network at all), `--read-only` root filesystem, a 64 MB
  `/tmp` tmpfs, `--user 65534:65534` (nobody), `--cap-drop ALL`,
  `--security-opt no-new-privileges`, `--memory`/`--memory-swap` 1 GB (no swap),
  `--cpus 1`, `--pids-limit 64` (no fork bombs), and the job directory mounted
  read-only. Per-test wall-clock timeouts are enforced inside, an overall timeout
  outside (`docker kill`). The container is removed afterwards (`--rm`).
- **docker.sock trade-off.** The worker needs to start sandbox containers, so it has
  the host's Docker socket. Access to that socket is equivalent to root on the host:
  whoever controls the worker process could start a privileged container. Why this is
  acceptable here:
  - the worker **never executes submitted code**; user code only runs inside the
    locked-down sandbox container above. The worker only reads the runner's
    size-capped JSON lines and feeds each test's stdout to `poker_run.check` (a
    pure-Python parser of moves);
  - the worker has no open ports and is not reachable from the internet; the
    internet-facing **api container does not have the socket**;
  - the worker runs as uid 10001 (in the docker group only), read-only root
    filesystem, no capabilities, `no-new-privileges`;
  - the containers cannot reach the instance role's credentials (IMDSv2 with hop limit 1).
  Stronger options if you need them: run the sandboxes with gVisor (`runsc` runtime),
  or rootless Docker for the judge. Both need changes to the sandbox command in
  `platform/server/prplatform/sandbox.py` and more testing; they are not needed for a
  normal student contest.
- **Patching.** Unattended security upgrades are on (installed by the bootstrap).
  Kernel updates need a reboot: `sudo reboot` once a month or when the login message
  says "System restart required" (the stack starts by itself after a reboot). Run
  `make rebuild` monthly to pick up fixed base images.
- **SSH.** Port 22 only from your own IP, **never 0.0.0.0/0**. Update the rule when your
  IP changes, or remove it entirely and use Session Manager (step 4's
  `AmazonSSMManagedInstanceCore`). Keep `pokerrun.pem` private.
- **Rate limits.** The app limits each user to one queued/running submission and
  `PR_FULL_PER_HOUR` full submissions per hour, caps upload size (`PR_MAX_SOURCE_KB`;
  Caddy additionally refuses API request bodies over 2 MB) and slows down failed
  logins per client IP (uvicorn trusts Caddy's `X-Forwarded-For`; the api port is not
  published, so only Caddy can reach it). Caddy itself has no request rate limiting;
  for a public, high-profile contest consider AWS WAF in front or the `caddy-ratelimit`
  plugin.
- **Secrets.** `.env` is mode 600 and git-ignored. User data contains no secrets
  (unless you used a GitHub token in `REPO_URL`; then delete the token after the
  contest). Never paste `.env` into chats or issues.
- **Headers.** HSTS, `nosniff`, a strict Referrer-Policy and a CSP that allows only
  this origin (plus Google Fonts) are set in the `Caddyfile`. Only `/play/` may be
  framed by other sites, and only by the origins in `FRAME_ANCESTORS`.

---

# Troubleshooting

| Symptom | Check |
| --- | --- |
| Browser says the certificate is invalid / connection refused | `dig +short pokerrun.example.com` = EIP? Ports 80 and 443 open in the security group? `make logs S=caddy` shows the ACME error. After fixing DNS: `make restart S=caddy`. |
| 502 on `/api/...` | `make ps` (api healthy?), `make logs S=api`. A config error in `.env` (e.g. a bad time or seed list) stops the api with a clear message. |
| Submissions stay `queued` | `make logs S=worker`. "permission denied ... docker.sock": `DOCKER_GID` in `.env` must equal `getent group docker | cut -d: -f3`, then `make up`. |
| Submissions end in `error` "could not start docker" / image not found | `make judge-image`, then rejudge them. |
| All tests `RE` with a mount or permission message | `/srv/pokerrun/jobs` must exist, be owned by 10001 and be mounted at the same path (see `docker-compose.yml`). |
| `make up` says `CHANGE_ME` | edit `.env` (step 8). |
| Backups missing in S3 | `sudo tail /var/log/pokerrun-backup.log`; "AccessDenied": the instance profile, bucket name and the `pokerrun/db/` prefix in the policy must match `BACKUP_BUCKET`/`BACKUP_PREFIX`. |
| The site still shows old pages after an update | `make rebuild` (the site is baked into the caddy image); HTML is served with `no-cache`, so a normal reload is enough afterwards. |
| The Next.js page cannot frame `/play/` | `FRAME_ANCESTORS` in `.env` must list your site's exact origin(s), then `make up` (caddy restarts). See `integrations/nextjs/README.md`. |
