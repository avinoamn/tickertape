# Thin wrappers over scripts/*.sh. Works from Git Bash, PowerShell or cmd.
# TAG must match the image tag in k8s/<service>*.yaml.
ifeq ($(OS),Windows_NT)
# Outside Git Bash, bash may be missing from PATH (or be the WSL launcher). Use Git's bash.
export PATH := C:/Program Files/Git/bin;$(PATH)
endif
SHELL := bash
SVC   ?= poller
TAG   ?= 0.1.0

# port-forward target: kubectl service in namespace tickertape -> localhost
PF_SVC  ?= postgres
PF_PORT ?= 5432

.PHONY: dashboards verify-dashboards monitoring-secrets monitoring-install eval-ner backfill dataset eval-laya help bootstrap build push deploy port-forward dev-up dev-down dev-poll status logs

help:
	@echo "make bootstrap                      one-time admin step: namespace + deployer RBAC + ~/.kube/tickertape (CHANGES CLUSTER STATE)"
	@echo "make build [SVC=poller TAG=0.1.0]   build image locally"
	@echo "make push  [SVC=poller TAG=0.1.0]   copy image to computa + import into k3s (asks for sudo password)"
	@echo "make deploy                         apply manifests on computa (CHANGES CLUSTER STATE; needs SEC_USER_AGENT)"
	@echo "make port-forward [PF_SVC=postgres PF_PORT=5432]   tunnel a cluster service to localhost"
	@echo "make dev-up | dev-down | dev-poll   local Docker Postgres / run poller against it"
	@echo "make eval-ner ARGS=\"sanity|cik|sample|gold FILE\"   NER quality checks against the dev DB"
	@echo "make backfill ARGS=\"cnbc|sec ...\"   step 5: add historical items to the DEV DB (training/backfill.py)"
	@echo "make dataset ARGS=\"select|status|batch|add|export\"   step 5: build the Laya training/gold dataset (training/build_dataset.py)"
	@echo "make eval-laya ARGS=\"predict --name base --model ...\"   step 5: cache a checkpoint's answers on the gold set (training/eval_laya.py); report: python training/eval_laya.py report base ft"
	@echo "make dashboards                      regenerate grafana/dashboards/*.json from grafana/gen_dashboards.py"
	@echo "make verify-dashboards              run every dashboard query through a live Grafana (local rig by default)"
	@echo "make monitoring-secrets             step 4 part 1: namespace, Grafana secrets, read-only DB role (CHANGES CLUSTER STATE)"
	@echo "make monitoring-install             step 4 part 2: Helm kube-prometheus-stack + ServiceMonitors + dashboards (CHANGES CLUSTER STATE)"
	@echo "make status | logs                  read-only look at the tickertape namespace"

bootstrap:
	scripts/bootstrap-access.sh

build:
	scripts/build.sh $(SVC) $(TAG)

push:
	scripts/ship.sh $(SVC) $(TAG)

deploy:
	scripts/deploy.sh

port-forward:
	scripts/kc.sh port-forward -n tickertape svc/$(PF_SVC) $(PF_PORT):$(PF_PORT)

dev-up:
	docker compose up -d --wait db

dev-down:
	docker compose down

dev-poll:
	docker compose run --rm --build poller

# Evaluate NER against the dev DB (see training/eval_ner.py): make eval-ner ARGS="sanity"
eval-ner:
	MSYS_NO_PATHCONV=1 docker compose run --rm --build --no-deps -v "$(CURDIR)/training:/app/training" -e PYTHONPATH=/app ner python training/eval_ner.py $(ARGS)

# Historical items into the DEV DB only (training/backfill.py): SEC_USER_AGENT="<name> <email>" make backfill ARGS="sec --count 500"
backfill:
	MSYS_NO_PATHCONV=1 docker compose run --rm --build --user root -v "$(CURDIR):/work" -w /work -e PYTHONPATH=/work --entrypoint python poller training/backfill.py $(ARGS)

# Laya dataset from the DEV DB (training/build_dataset.py), runs in the laya image; `add` reads labels from stdin
dataset:
	MSYS_NO_PATHCONV=1 docker compose run --rm -T --no-deps --user root -v "$(CURDIR):/work" -w /work -e PYTHONPATH=/work --entrypoint python laya training/build_dataset.py $(ARGS)

# Laya checkpoint predictions on the gold set (training/eval_laya.py predict), laya image, HF cache volume, optional HF_TOKEN
eval-laya:
	MSYS_NO_PATHCONV=1 docker compose run --rm -T --no-deps --user root -v "$(CURDIR):/work" -w /work -e PYTHONPATH=/work -e HF_TOKEN --entrypoint python laya training/eval_laya.py $(ARGS)

status:
	scripts/kc.sh get all,pvc,cronjob -n tickertape

logs:
	scripts/kc.sh logs -n tickertape -l job-name --tail=50 --prefix

# Step 4. Local rig: docker compose --profile monitoring up -d (see docker-compose.yml), then make verify-dashboards.
dashboards:
	python grafana/gen_dashboards.py

verify-dashboards:
	python grafana/verify_dashboards.py

monitoring-secrets:
	scripts/create-monitoring-secrets.sh

monitoring-install:
	scripts/install-monitoring.sh
