.PHONY: setup secrets db-up db-down db-shell migrate seed api test lint typecheck audit smoke dbt airflow airflow-trigger airflow-state ml-serving-trigger ml-retrain-trigger ml-state ml-benchmark ml-forecast eta-benchmark clean

API_PORT ?= 8010

setup:            ## create venv + install dev dependencies
	python -m venv .venv
	.venv/Scripts/python -m pip install --upgrade pip
	.venv/Scripts/python -m pip install -r requirements-dev.txt

db-up:            ## start the PostgreSQL container (production database)
	.venv/Scripts/python scripts/ensure_secrets.py 2>/dev/null || python scripts/ensure_secrets.py
	docker compose up -d postgres

db-down:          ## stop the PostgreSQL container (data persists in the pgdata volume)
	docker compose stop postgres

db-shell:         ## interactive psql inside the Postgres container
	docker compose exec postgres psql -U smartops -d smart_delivery_ops

secrets:          ## generate the local secrets files (secrets/, gitignored)
	.venv/Scripts/python scripts/ensure_secrets.py 2>/dev/null || python scripts/ensure_secrets.py

migrate:         ## apply the Alembic chain to the ops DB (schema owner, #28)
	docker compose run --rm migrate

seed:            ## seed the database with demo data (needs migrations first: make migrate)
	.venv/Scripts/python scripts/seed_demo_data.py

docs-ingest:      ## (re)ingest the platform docs into the copilot's knowledge store
	.venv/Scripts/python scripts/ingest_docs.py

vault-ingest:     ## sync an Obsidian vault into the copilot's knowledge store (VAULT=path, default ~/Documents/SmartDeliveryVault)
	.venv/Scripts/python scripts/ingest_obsidian.py --vault "$(VAULT)"

api:             ## run the API locally with reload (Swagger at http://127.0.0.1:$(API_PORT)/docs)
	.venv/Scripts/python -m uvicorn apps.api.main:app --reload --port $(API_PORT)

test:            ## run the full test suite with coverage
	.venv/Scripts/python -m pytest --cov=apps --cov=core --cov=domain

lint:            ## ruff check + format verification
	.venv/Scripts/python -m ruff check .
	.venv/Scripts/python -m ruff format --check .

typecheck:       ## static type checking (mypy)
	.venv/Scripts/python -m mypy apps core domain scripts tests

audit:           ## dependency vulnerability audit (production requirements)
	.venv/Scripts/python -m pip_audit -r requirements.txt

smoke:           ## end-to-end smoke test against a running API (make api first)
	.venv/Scripts/python scripts/smoke_test.py

# #54: the checked-out branch picks the dbt output schema - main builds
# the production `analytics` marts, every other branch lands in
# `analytics_draft` (branches share the draft schema by design).
DBT_BRANCH ?= $(shell git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)
DBT_SCHEMA ?= $(if $(filter main,$(DBT_BRANCH)),analytics,analytics_draft)

dbt:             ## build + test the warehouse marts (schema follows the branch: main->analytics, else draft)
	@echo "dbt output schema: $(DBT_SCHEMA) (branch $(DBT_BRANCH))"
	DBT_SCHEMA=$(DBT_SCHEMA) docker compose run --rm dbt build
	DBT_SCHEMA=$(DBT_SCHEMA) docker compose run --rm dbt source freshness

airflow-trigger: ## trigger the warehouse DAG immediately (UI: http://localhost:8081)
	docker compose exec airflow-scheduler airflow dags trigger warehouse_daily_build

airflow-state:   ## show recent runs and task states of the warehouse DAG
	docker compose exec airflow-scheduler airflow dags list-runs -d warehouse_daily_build

ml-serving-trigger: ## trigger the hourly forecast-serving DAG now (else it fires at :00)
	docker compose exec airflow-scheduler airflow dags trigger ml_forecast_serving

ml-retrain-trigger: ## trigger the daily retrain/benchmark DAG now (else 04:15 UTC daily)
	docker compose exec airflow-scheduler airflow dags trigger ml_retrain_benchmark

ml-state:        ## show recent runs of both ML DAGs
	docker compose exec airflow-scheduler airflow dags list-runs -d ml_forecast_serving
	docker compose exec airflow-scheduler airflow dags list-runs -d ml_retrain_benchmark

ml-benchmark:    ## run the demand-forecasting benchmark (logs to MLflow, UI :5100)
	docker compose up -d mlflow
	GIT_SHA=$$(git rev-parse --short HEAD) docker compose run --rm ml-bench

ml-forecast:     ## refresh the next-24h zone-hour demand forecasts the API prices against
	docker compose up -d mlflow
	GIT_SHA=$$(git rev-parse --short HEAD) docker compose run --rm ml-bench python pipelines/forecast_demand.py

eta-benchmark:   ## run the ETA benchmark (heuristic vs OSRM vs LightGBM; logs to MLflow)
	docker compose up -d mlflow osrm
	GIT_SHA=$$(git rev-parse --short HEAD) docker compose run --rm ml-bench python training/benchmark_eta.py

clean:           ## remove caches and local databases
	rm -rf .pytest_cache .ruff_cache **/__pycache__
	rm -f *.db
