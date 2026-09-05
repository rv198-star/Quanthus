FROM python:3.11-slim

ARG QUANTHUS_CODE_REVISION
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QUANTHUS_CODE_REVISION=${QUANTHUS_CODE_REVISION}

WORKDIR /app

COPY pyproject.toml README.md ./
COPY config ./config
COPY src ./src

RUN python -m pip install --no-cache-dir ".[p1,platform]" "baostock>=0.9.3,<0.10" "pandas>=2.2,<3"

ENTRYPOINT ["python", "-m", "quanthus"]
