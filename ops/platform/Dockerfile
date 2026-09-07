FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GUNICORN_CMD_ARGS="--worker-tmp-dir=/tmp --no-control-socket"

RUN useradd --create-home --uid 10001 quanthus
WORKDIR /app

COPY pyproject.toml README.md ./
COPY config/platform-features.v1.json config/platform-kernel-gates.v1.json config/product-research-catalog.v1.json config/product-candidate-evidence.v1.json config/product-expert-evidence.v1.json config/data-indicator-catalog-annotations.v1.json config/catalog-business-metadata.v1.json config/catalog-lineage.v1.json config/data-indicator-catalog-snapshot.v1.json config/data-indicator-catalog-sources.v1.json config/p3-baseline-features.research.v1.json config/p3-governed-feature-extension.research.v1.json config/p3-governed-labels.research.v1.json config/structure-semantic-catalog.v1.json config/structure-facts-v2-blueprint.v1.json ./config/
COPY config/pipelines/ods-base-final-through-20260731.v2.json config/pipelines/dwd-domain-model.v1.json config/pipelines/dwd-domain-model.v1.1-extension.json ./config/pipelines/
COPY config/releases/v2-build202607.json ./config/releases/
COPY config/expert-strategies/p4-trend-risk.v1.0.1.json ./config/expert-strategies/
COPY config/expert-evaluations/lifecycle/candidate-p4-trend-risk-1.0.1.inconclusive.v1.json ./config/expert-evaluations/lifecycle/
RUN find ./config -type d -exec chmod 0555 {} + \
    && find ./config -type f -exec chmod 0444 {} +
RUN mkdir -p ./src/quanthus/expert_evaluation
COPY src/quanthus/__init__.py \
    src/quanthus/data_catalog.py \
    src/quanthus/catalog_presentation.py \
    src/quanthus/catalog_lineage.py \
    src/quanthus/policy.py \
    src/quanthus/strategy_attribution.py \
    src/quanthus/strategy_attribution_pipeline.py \
    src/quanthus/strategy_attribution_planning.py \
    src/quanthus/strategy_attribution_publication.py \
    ./src/quanthus/
COPY src/quanthus/expert_evaluation/__init__.py \
    src/quanthus/expert_evaluation/contracts.py \
    src/quanthus/expert_evaluation/product_view.py \
    src/quanthus/expert_evaluation/release_verification.py \
    src/quanthus/expert_evaluation/runner.py \
    src/quanthus/expert_evaluation/serialization.py \
    ./src/quanthus/expert_evaluation/
COPY src/quanthus/platform ./src/quanthus/platform
RUN python -m pip install --no-cache-dir ".[platform]"

EXPOSE 8080

CMD ["python", "-m", "quanthus.platform.launcher"]
