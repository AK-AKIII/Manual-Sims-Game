# Common operations for the Poker Run deployment. Run on the server, from anywhere:
#   make -C /opt/pokerrun/platform/deploy <target>
# or cd into platform/deploy and run `make <target>`. `make help` lists targets.

DEPLOY_DIR := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
REPO_DIR   := $(abspath $(DEPLOY_DIR)/../..)
COMPOSE    := docker compose --project-directory $(DEPLOY_DIR) -f $(DEPLOY_DIR)/docker-compose.yml
ENV_FILE   := $(DEPLOY_DIR)/.env
env_get     = $(shell grep -E '^$(1)=' $(ENV_FILE) 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '"')
JUDGE_IMAGE ?= $(or $(call env_get,PR_JUDGE_IMAGE),poker-run-judge)
DOMAIN      ?= $(call env_get,DOMAIN)
S ?=
N ?= 1
FROM ?= latest

.DEFAULT_GOAL := help
.PHONY: help check-env up down restart ps logs rebuild update judge-image reload \
        backup restore workers export smoke shell sqlite

help: ## list targets
	@grep -E '^[a-z-]+:.*## ' $(lastword $(MAKEFILE_LIST)) | sed -e 's/:.*## /\t/' | expand -t 14

check-env: ## refuse to start with placeholder secrets or a missing .env
	@test -f $(ENV_FILE) || { echo "missing $(ENV_FILE): cp env.example .env (or run bootstrap.sh)"; exit 1; }
	@if grep -nE '^[A-Z_]+=CHANGE_ME' $(ENV_FILE); then echo "replace the CHANGE_ME values above in $(ENV_FILE)"; exit 1; fi
	@if grep -qE '^PR_HIDDEN_SEEDS=1001-1050$$' $(ENV_FILE); then echo "PR_HIDDEN_SEEDS is the public dev default"; exit 1; fi
	@if grep -qE '^DOMAIN=(pokerrun\.example\.com)?$$' $(ENV_FILE); then echo "set DOMAIN in $(ENV_FILE)"; exit 1; fi

up: check-env ## build if needed and start everything (also applies .env changes)
	$(COMPOSE) up -d --build

down: ## stop and remove the containers (data, certificates and backups stay)
	$(COMPOSE) down

restart: ## restart containers without rebuilding: make restart [S=worker]
	$(COMPOSE) restart $(S)

ps: ## container status and health
	$(COMPOSE) ps

logs: ## follow logs: make logs [S=api|worker|caddy]
	$(COMPOSE) logs -f --tail=200 $(S)

rebuild: check-env ## rebuild images from the checkout (pulls newer base images) and restart
	$(COMPOSE) build --pull
	$(COMPOSE) up -d

update: ## git pull, rebuild the judge image and the stack
	git -C $(REPO_DIR) pull --ff-only
	$(MAKE) -f $(lastword $(MAKEFILE_LIST)) judge-image rebuild

judge-image: ## build the sandbox image the worker runs for every submission
	docker build --pull -t $(JUDGE_IMAGE) $(REPO_DIR)/platform/judge

reload: ## apply Caddyfile edits without downtime
	$(COMPOSE) exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile

backup: ## database backup now (local + S3)
	sudo $(DEPLOY_DIR)/backup.sh

restore: ## restore: make restore FROM=latest | FROM=<s3 key> | FROM=<local .db.gz>
	sudo $(DEPLOY_DIR)/restore.sh $(FROM)

workers: ## set judging threads (PR_WORKERS) and restart the worker: make workers N=2
	@test -f $(ENV_FILE)
	@if grep -qE '^PR_WORKERS=' $(ENV_FILE); then sed -i -e 's/^PR_WORKERS=.*/PR_WORKERS=$(N)/' $(ENV_FILE); else echo 'PR_WORKERS=$(N)' >> $(ENV_FILE); fi
	$(COMPOSE) up -d worker

export: ## save the current public leaderboard as JSON + CSV under <root>/backups/exports
	$(COMPOSE) exec -T api python -c "$$EXPORT_PY"

smoke: ## quick external checks against https://$(DOMAIN)
	curl -fsS https://$(DOMAIN)/api/contest; echo
	curl -fsS -o /dev/null -w 'landing %{http_code}\n' https://$(DOMAIN)/
	curl -fsS -o /dev/null -w 'play    %{http_code}\n' https://$(DOMAIN)/play/
	curl -fsS -o /dev/null -w 'video   %{http_code} %{size_download} bytes\n' https://$(DOMAIN)/media/poker_run_explainer.mp4
	curl -fsS https://$(DOMAIN)/api/leaderboard | head -c 400; echo

shell: ## a shell in the api container
	$(COMPOSE) exec api bash

sqlite: ## read-only SQL prompt on the live database (python sqlite3 inside the api container)
	$(COMPOSE) exec api python -c "import sqlite3,code; c=sqlite3.connect('file:/data/platform.db?mode=ro',uri=True); q=lambda s,*a: [print(r) for r in c.execute(s,a)]; print('q(\"SELECT ...\")'); code.interact(local={'c':c,'q':q})"

define EXPORT_PY
import csv, json, os, time, urllib.request
rows = json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/leaderboard", timeout=10))
stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
os.makedirs("/backups/exports", exist_ok=True)
base = f"/backups/exports/leaderboard-{stamp}"
json.dump(rows, open(base + ".json", "w"), indent=1)
cols = ["rank", "username", "relative_total", "raw_total", "tests_ac", "submitted", "submissions"]
with open(base + ".csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows.get("rows", []))
print("frozen:", rows.get("frozen"), "rows:", len(rows.get("rows", [])), "->", base + ".{json,csv}")
endef
export EXPORT_PY
