PYTHON ?= python3
VENV ?= .venv

.PHONY: bootstrap bootstrap-platform bootstrap-p1 bootstrap-ods doctor test platform-check platform-postgres-check platform-docker-acceptance platform-image platform-web-image platform-web-check platform-recovery-backup platform-recovery-restore replay-image candidate-batch compile planning-check check lint format data-audit curate-none coverage-none s1-coverage p1-evidence-check publish-snapshot pipeline-check schedule-check lane-shadow-matrix

bootstrap:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/python -m pip install -e ".[dev]"

bootstrap-platform:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/python -m pip install -e ".[dev,platform]"

bootstrap-p1:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/python -m pip install -e ".[dev,p1]"

bootstrap-ods:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/python -m pip install -e ".[dev,ods]"

doctor:
	PYTHONPATH=src $(PYTHON) -m quanthus doctor

test:
	PYTHONPATH=src $(PYTHON) -m pytest -q

platform-check:
	PYTHONPATH=src $(VENV)/bin/python -m pytest -q \
		tests/platform \
		tests/contract/test_platform_kernel_planning.py

platform-postgres-check:
	@test -n "$${QUANTHUS_TEST_POSTGRES_DSN:-}" || \
		(echo "set QUANTHUS_TEST_POSTGRES_DSN to a disposable PostgreSQL database"; exit 2)
	PYTHONPATH=src $(VENV)/bin/python -m pytest -q \
		tests/platform/test_postgres_acceptance.py

platform-docker-acceptance:
	./ops/bin/run-platform-docker-acceptance

platform-image:
	./ops/bin/build-platform-image

platform-web-image:
	./ops/bin/build-platform-web-image

platform-web-check:
	cd web && npm test

platform-recovery-backup:
	@test -n "$${QUANTHUS_RECOVERY_PACKAGE:-}" || \
		(echo "set QUANTHUS_RECOVERY_PACKAGE to a new absolute package path"; exit 2)
	./ops/bin/backup-platform-recovery "$${QUANTHUS_RECOVERY_PACKAGE}"

platform-recovery-restore:
	@test -n "$${QUANTHUS_RECOVERY_PACKAGE:-}" || \
		(echo "set QUANTHUS_RECOVERY_PACKAGE to an existing package path"; exit 2)
	@test -n "$${QUANTHUS_RESTORE_PROJECT:-}" || \
		(echo "set QUANTHUS_RESTORE_PROJECT to quanthus-pk6-restore-NAME"; exit 2)
	@test -n "$${QUANTHUS_PLATFORM_ENV:-}" || \
		(echo "set QUANTHUS_PLATFORM_ENV to the absolute mode-0600 secret env path"; exit 2)
	./ops/bin/restore-platform-recovery \
		"$${QUANTHUS_RECOVERY_PACKAGE}" \
		"$${QUANTHUS_RESTORE_PROJECT}" \
		"$${QUANTHUS_PLATFORM_ENV}" \
		"$${QUANTHUS_RECOVERY_RECEIPT:-artifacts/platform-kernel/recovery-receipt.txt}"

replay-image:
	./ops/bin/build-replay-image

candidate-batch:
	./ops/bin/run-candidate-batch

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
		--universe-policy config/p1-universe.v2.json \
		--output "$${QUANTHUS_CURATED_PATH:-/home/ubuntu/data/quanthus/curated/bars_daily_none.parquet}" \
		--metadata-output artifacts/curated-daily-none.json

coverage-none:
	@test -n "$${QUANTHUS_KNOWLEDGE_CUTOFF:-}" || (echo "set QUANTHUS_KNOWLEDGE_CUTOFF=YYYY-MM-DD"; exit 2)
	$(VENV)/bin/python -m quanthus build-tradability-coverage \
		--root "$${QUANTHUS_DATA_ROOT:-/home/ubuntu/data/astock}" \
		--knowledge-cutoff "$${QUANTHUS_KNOWLEDGE_CUTOFF}" \
		--output "$${QUANTHUS_COVERAGE_PATH:-/home/ubuntu/data/quanthus/curated/tradability_coverage_none.parquet}" \
		--metadata-output artifacts/tradability-coverage-none.json

s1-coverage:
	@test -n "$${QUANTHUS_KNOWLEDGE_CUTOFF:-}" || (echo "set QUANTHUS_KNOWLEDGE_CUTOFF=YYYY-MM-DD"; exit 2)
	$(VENV)/bin/python -m quanthus delisted-coverage \
		--root "$${QUANTHUS_DATA_ROOT:-/home/ubuntu/data/astock}" \
		--knowledge-cutoff "$${QUANTHUS_KNOWLEDGE_CUTOFF}" \
		--output "$${QUANTHUS_S1_REPORT_PATH:-artifacts/s1-delisted-coverage.json}"

p1-evidence-check:
	@test -n "$${QUANTHUS_P1_EVIDENCE:-}" || (echo "set QUANTHUS_P1_EVIDENCE=/absolute/path/to/evidence.json"; exit 2)
	$(VENV)/bin/python -m quanthus p1-acceptance-check --evidence "$${QUANTHUS_P1_EVIDENCE}"

publish-snapshot:
	@test -n "$${QUANTHUS_SNAPSHOT_PLAN:-}" || (echo "set QUANTHUS_SNAPSHOT_PLAN=/absolute/path/to/plan.json"; exit 2)
	$(VENV)/bin/python -m quanthus publish-snapshot \
		--plan "$${QUANTHUS_SNAPSHOT_PLAN}" \
		--catalog-root "$${QUANTHUS_SNAPSHOT_CATALOG:-/home/ubuntu/data/quanthus/snapshots}" \
		--metadata-output artifacts/p1-snapshot.json

pipeline-check:
	$(VENV)/bin/python -m quanthus pipeline-check \
		--config config/pipelines/t-close-plus-5h.v1.json

schedule-check:
	$(VENV)/bin/python -m quanthus schedule-check \
		--config config/schedules/hermes-ods-schedule.v1.json

lane-shadow-matrix:
	PYTHONPATH=src $(PYTHON) -m quanthus.cli lane-shadow-matrix \
		--config config/schedules/hermes-ods-schedule.v1.json \
		--open-date "$${OPEN_DATE:-2026-07-24}" \
		--closed-date "$${CLOSED_DATE:-2026-07-25}" \
		--weekly-date "$${WEEKLY_DATE:-2026-07-24}" \
		--event-date "$${EVENT_DATE:-2026-07-24}" \
		--home "$${ASTOCK_HOME:-/root/work/astock/data}" \
		--shadow-root "$${HERMES_ODS_SHADOW_ROOT:-/root/work/astock/data/shadow/hermes-ods-v1}" \
		--calendar "$${HERMES_ODS_CALENDAR:-/root/work/astock/data/ref_trading_calendar.parquet}"
