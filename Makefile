PYTHON ?= python3
VENV ?= .venv

.PHONY: bootstrap bootstrap-p1 doctor test compile planning-check check lint format

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
