.PHONY: setup db-up db-down db-shell seed api test lint typecheck audit smoke dbt airflow ml-benchmark clean

API_PORT ?= 8010

setup:            ## create venv + install dev dependencies
	python -m venv .venv
	.venv/Scripts/python -m pip install --upgrade pip
	.venv/Scripts/python -m pip install -r requirements-dev.txt

db-up:            ## start the PostgreSQL container (production database)
	docker compose up -d postgres

db-down:          ## stop the PostgreSQL container (data persists in the pgdata volume)
	docker compose stop postgres

db-shell:         ## interactive psql inside the Postgres container
	docker compose exec postgres psql -U smartops -d smart_delivery_ops

seed:            ## seed the database with demo data (Tunis metro area)
	.venv/Scripts/python scripts/seed_demo_data.py

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

dbt:             ## build + test the warehouse marts, then check source freshness
	docker compose run --rm dbt build
	docker compose run --rm dbt source freshness

airflow-trigger: ## trigger the warehouse DAG immediately (UI: http://localhost:8081)
	docker compose exec airflow-scheduler airflow dags trigger warehouse_daily_build

airflow-state:   ## show recent runs and task states of the warehouse DAG
	docker compose exec airflow-scheduler airflow dags list-runs -d warehouse_daily_build

ml-benchmark:    ## run the demand-forecasting benchmark (logs to MLflow, UI :5100)
	docker compose up -d mlflow
	GIT_SHA=$$(git rev-parse --short HEAD) docker compose run --rm ml-bench

clean:           ## remove caches and local databases
	rm -rf .pytest_cache .ruff_cache **/__pycache__
	rm -f *.db
