FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GUNICORN_CMD_ARGS="--worker-tmp-dir=/tmp --no-control-socket"

RUN useradd --create-home --uid 10001 quanthus
WORKDIR /app

COPY pyproject.toml README.md ./
COPY config/platform-features.v1.json config/platform-kernel-gates.v1.json config/product-research-catalog.v1.json config/product-candidate-evidence.v1.json config/product-expert-evidence.v1.json ./config/
COPY config/expert-strategies/p4-trend-risk.v1.0.1.json ./config/expert-strategies/
COPY config/expert-evaluations/lifecycle/candidate-p4-trend-risk-1.0.1.inconclusive.v1.json ./config/expert-evaluations/lifecycle/
RUN chmod 0444 ./config/platform-features.v1.json ./config/platform-kernel-gates.v1.json ./config/product-research-catalog.v1.json ./config/product-candidate-evidence.v1.json ./config/product-expert-evidence.v1.json ./config/expert-strategies/p4-trend-risk.v1.0.1.json ./config/expert-evaluations/lifecycle/candidate-p4-trend-risk-1.0.1.inconclusive.v1.json
RUN mkdir -p ./src/quanthus/expert_evaluation
COPY src/quanthus/__init__.py \
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
