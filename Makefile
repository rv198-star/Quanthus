PYTHON ?= python3
VENV ?= .venv

.PHONY: bootstrap bootstrap-p1 doctor test compile planning-check check lint format data-audit curate-none coverage-none

bootstrap:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/python -m pip install -e ".[dev]"

bootstrap-p1:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/python -m pip install -e ".[dev,p1]"

doctor:
	PYTHONPATH=src $(PYTHON) -m quanthus doctor

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -t . -v

compile:
	PYTHONPATH=src $(PYTHON) -m compileall -q src tests

planning-check:
	$(PYTHON) scripts/sync_github_planning.py >/dev/null

check: doctor compile test planning-check

lint:
	$(VENV)/bin/python -m ruff check .

format:
	$(VENV)/bin/python -m ruff format .

data-audit:
	$(VENV)/bin/python -m quanthus data-audit \
		--root "$${QUANTHUS_DATA_ROOT:-/home/ubuntu/data/astock}" \
		--output artifacts/p1-data-audit.json

curate-none:
	$(VENV)/bin/python -m quanthus curate-daily \
		--root "$${QUANTHUS_DATA_ROOT:-/home/ubuntu/data/astock}" \
		--universe-policy config/p1-universe.v1.json \
		--output "$${QUANTHUS_CURATED_PATH:-/home/ubuntu/data/quanthus/curated/bars_daily_none.parquet}" \
		--metadata-output artifacts/curated-daily-none.json

coverage-none:
	@test -n "$${QUANTHUS_KNOWLEDGE_CUTOFF:-}" || (echo "set QUANTHUS_KNOWLEDGE_CUTOFF=YYYY-MM-DD"; exit 2)
	$(VENV)/bin/python -m quanthus build-tradability-coverage \
		--root "$${QUANTHUS_DATA_ROOT:-/home/ubuntu/data/astock}" \
		--knowledge-cutoff "$${QUANTHUS_KNOWLEDGE_CUTOFF}" \
		--output "$${QUANTHUS_COVERAGE_PATH:-/home/ubuntu/data/quanthus/curated/tradability_coverage_none.parquet}" \
		--metadata-output artifacts/tradability-coverage-none.json
