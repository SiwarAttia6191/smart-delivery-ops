.PHONY: setup seed api test lint smoke clean

setup:            ## create venv + install dev dependencies
	python -m venv .venv
	.venv/Scripts/python -m pip install --upgrade pip
	.venv/Scripts/python -m pip install -r requirements-dev.txt

seed:            ## seed the local database with demo data (Tunis metro area)
	.venv/Scripts/python scripts/seed_demo_data.py

api:             ## run the API locally with reload (Swagger at /docs)
	.venv/Scripts/python -m uvicorn apps.api.main:app --reload --port 8000

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
