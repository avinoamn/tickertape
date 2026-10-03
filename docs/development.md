# Development

Everything runs locally in Docker against a throwaway Postgres. Nothing in this guide touches a cluster.

## Prerequisites

- Docker with Compose. Give Docker at least **4 GB** of memory if you run both models; an out-of-memory kill shows up as a container that just disappears with no error (`docker ps -a`, `docker stats`).
- `make` and a POSIX shell. On Windows use Git Bash (with `make` from Chocolatey); the Makefile points `bash` at Git's.
- Python 3 on the host is only needed for a few helper scripts that use the standard library (`grafana/gen_dashboards.py`, `training/eval_laya.py report`). The services and the ML code run in containers.

## The local stack

`docker-compose.yml` defines everything for local work. Its credentials (`tickertape` / `dev`) are for a database that is only reachable on `127.0.0.1`.

| Service | Start with | Where |
|---|---|---|
| Postgres 16 with the chart's `schema.sql` applied on first start | `make dev-up` | `localhost:5432` |
| poller (runs once and exits) | `make dev-poll` | needs `SEC_USER_AGENT` for the SEC feed |
| ner | `docker compose up -d --build ner` | UI <http://localhost:7860>, metrics `:8000/metrics` |
| laya | `docker compose up -d --build laya` | UI <http://localhost:7861>, metrics `:8001/metrics` |
| Grafana and Prometheus (optional) | `docker compose --profile monitoring up -d` | <http://localhost:3000>, <http://localhost:9090> |

```sh
make dev-up
SEC_USER_AGENT="Your Name you@example.com" make dev-poll
docker compose up -d --build ner
docker compose up -d --build laya
docker compose exec db psql -U tickertape -c "select status, count(*) from items group by 1"
```

- `SEC_USER_AGENT` must be `"<name> <email>"` with a real contact. The SEC blocks anonymous clients. Without it, set `enabled: false` on the `sec_8k` feed in `services/poller/feeds.yaml` for your local runs.
- The first start of ner and laya downloads the models into Docker volumes (`hfcache`, `hfcache-laya`) and takes a few minutes. Later starts take about a minute.
- Laya uses the public base model by default. To try another one set `LAYA_MODEL` (and for a private repo `HF_TOKEN`) in your shell before `docker compose up`.
- Laya is slow on CPU (several seconds per item). Stop it with `docker compose stop laya` when you do not need it.
- `make dev-down` stops everything; add `-v` to `docker compose down` to also delete the database volume.

## Repository conventions

- Services are built from the **repository root** so that `common/` is in the Docker context: `docker build -f services/<svc>/Dockerfile .` (or `make build SVC=<svc> TAG=<tag>`).
- Dependencies are pinned in each service's `requirements.txt`. torch is installed from the CPU wheel index.
- All configuration comes from environment variables; secrets never go in the repository.
- Files use LF line endings (`.gitattributes`).
- `grafana/gen_dashboards.py` is the source of truth for the dashboards. Edit it, run `make dashboards`, and commit the generated JSON.

## Checking your changes

```sh
make lint                       # ruff, in a python:3.12 container
make test                       # pytest, in a python:3.12 container, against the dev database (make dev-up)
make chart-check                # helm lint + render + kubeconform, in containers
make dashboards                 # regenerate grafana/dashboards/*.json
docker compose --profile monitoring up -d
make verify-dashboards          # runs every panel query against the local Grafana and reports errors / empty panels
```

Panels that depend on kube-state-metrics or cAdvisor (poller job health, laya memory and CPU) are empty locally by design; check those on a cluster.

### Tests

`tests/` holds the pytest suite (about 85 tests, a few seconds). It covers the logic that does not need a model: NER entity cleaning and focus-ticker resolution, Laya state building and answer flattening, poller text cleaning, feed expansion and fetching (against a fake HTTP server, including the conditional-GET path), the Postgres queue (claiming with `SKIP LOCKED`, the retry rule, the schema being re-appliable) and the question definitions. torch, Laya and GLiNER are never imported, so the suite stays light.

The database tests use a real Postgres, not mocks, because the queue behaviour *is* the SQL. They read `TEST_DATABASE_URL` and are skipped without it. Each test creates its own throw-away schema and drops it afterwards, so running them against your dev database is safe (`make test` does exactly that). To run pytest directly you need Python 3.12 and `pip install -r requirements-dev.txt`.

CI (`.github/workflows/ci.yml`) runs on every pull request and on pushes to `main`: ruff and shellcheck, the tests against a Postgres service container, a check that the committed dashboards match the generator, kubeconform on `k8s/`, and a build (without pushing) of each service image. The single job `CI passed` summarises them.

NER quality checks against labelled items live in `training/eval_ner.py` (`make eval-ner ARGS="sanity"`, see [training/README.md](../training/README.md)); they are measurements, not pass/fail tests.

## Using the services directly

- ner UI: paste a headline and see highlighted entities, JSON and the resolved focus ticker.
- laya UI: edit the state JSON and the questions JSON and see the answers with their confidence.
- `psql` examples:

```sql
-- latest decisions with their model revision
select i.title, d.question, d.answer, round(d.confidence::numeric, 2) as conf, d.model_rev
from decisions d join items i on i.id = d.item_id
order by d.created_at desc limit 20;
```
