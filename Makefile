# Thin wrappers over scripts/*.sh. Works from Git Bash, PowerShell or cmd.
# TAG defaults to the version in services/<svc>/VERSION.
ifeq ($(OS),Windows_NT)
# Outside Git Bash, bash may be missing from PATH (or be the WSL launcher). Use Git's bash.
export PATH := C:/Program Files/Git/bin;$(PATH)
endif
SHELL := bash
SVC   ?= poller
TAG   ?=

# port-forward target: kubectl service in namespace tickertape -> localhost
PF_SVC  ?= postgres
PF_PORT ?= 5432

.PHONY: lint test chart-check dashboards verify-dashboards monitoring-secrets monitoring-install eval-ner backfill dataset eval-laya help bootstrap build push deploy port-forward dev-up dev-down dev-poll status logs

help:
	@echo "make bootstrap                      one-time admin step: namespace + deployer RBAC + ~/.kube/tickertape (CHANGES CLUSTER STATE)"
	@echo "make build [SVC=poller TAG=...]    build tickertape/<svc>:<TAG> locally (TAG defaults to services/<svc>/VERSION)"
	@echo "make push  [SVC=poller TAG=...]    copy image to computa + import into k3s (asks for sudo password)"
	@echo "make deploy [HELM_ARGS=...]        helm upgrade --install the chart on computa (CHANGES CLUSTER STATE; SEC_USER_AGENT for the first run)"
	@echo "make port-forward [PF_SVC=postgres PF_PORT=5432]   tunnel a cluster service to localhost"
	@echo "make dev-up | dev-down | dev-poll   local Docker Postgres / run poller against it"
	@echo "make eval-ner ARGS=\"sanity|cik|sample|gold FILE\"   NER quality checks against the dev DB"
	@echo "make backfill ARGS=\"cnbc|sec ...\"   step 5: add historical items to the DEV DB (training/backfill.py)"
	@echo "make dataset ARGS=\"select|status|batch|add|export\"   step 5: build the Laya training/gold dataset (training/build_dataset.py)"
	@echo "make eval-laya ARGS=\"predict --name base --model ...\"   step 5: cache a checkpoint's answers on the gold set (training/eval_laya.py); report: python training/eval_laya.py report base ft"
	@echo "make chart-check              helm lint + template (several value sets) + kubeconform, in containers"
	@echo "make lint | test               ruff and pytest in a python:3.12 container (test uses the dev DB from make dev-up in a throw-away schema)"
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
	scripts/deploy.sh $(HELM_ARGS)

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

# Lint and tests run in a container: the code needs Python 3.12 and the host Python is often older.
# `test` uses the dev database from `make dev-up`; every test gets its own throw-away schema, so dev data is untouched.
PYIMG ?= python:3.12-slim
lint:
	MSYS_NO_PATHCONV=1 docker run --rm -v "$(CURDIR):/work" -w /work $(PYIMG) sh -c "pip install -q -r requirements-dev.txt 2>&1 | grep -iv -e notice -e warning; ruff check ."

test:
	MSYS_NO_PATHCONV=1 docker run --rm -v "$(CURDIR):/work" -w /work --add-host host.docker.internal:host-gateway 	  -e TEST_DATABASE_URL=$${TEST_DATABASE_URL:-postgresql://tickertape:dev@host.docker.internal:5432/tickertape} 	  $(PYIMG) sh -c "pip install -q -r requirements-dev.txt 2>&1 | grep -iv -e notice -e warning; pytest -q"

# Validate the chart without a cluster: lint, then render a few value sets and check them against the Kubernetes schemas.
HELM_IMG ?= alpine/helm:3.21.2
KUBECONFORM_IMG ?= ghcr.io/yannh/kubeconform:v0.8.0
chart-check:
	MSYS_NO_PATHCONV=1 docker run --rm -v "$(CURDIR):/work" -w /work $(HELM_IMG) lint charts/tickertape
	for sets in "" "--set ingress.enabled=false --set serviceMonitors.enabled=false" "--set secrets.hfToken= --set laya.revision= --set laya.model=convaiinnovations/laya --set ner.ui.type=ClusterIP --set laya.ui.type=ClusterIP" ; do 	  MSYS_NO_PATHCONV=1 docker run --rm -v "$(CURDIR):/work" -w /work $(HELM_IMG) template tickertape charts/tickertape -n tickertape --api-versions monitoring.coreos.com/v1 $$sets 	    | docker run --rm -i $(KUBECONFORM_IMG) -strict -ignore-missing-schemas -summary - || exit 1; 	done

