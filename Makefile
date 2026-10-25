.PHONY: setup db-up db-down db-shell seed api test lint smoke clean

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

test:            ## run the full test suite
	.venv/Scripts/python -m pytest

lint:            ## ruff check + format verification
	.venv/Scripts/python -m ruff check .
	.venv/Scripts/python -m ruff format --check .

smoke:           ## end-to-end smoke test against a running API (make api first)
	.venv/Scripts/python scripts/smoke_test.py

clean:           ## remove caches and local databases
	rm -rf .pytest_cache .ruff_cache **/__pycache__
	rm -f *.db
