FROM python:3.11-slim

ARG QUANTHUS_CODE_REVISION
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QUANTHUS_CODE_REVISION=${QUANTHUS_CODE_REVISION}

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client rsync \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY config ./config
COPY docs ./docs
COPY src ./src

RUN python -m pip install --no-cache-dir ".[p1,platform]" "baostock>=0.9.3,<0.10" "pandas>=2.2,<3"

ENTRYPOINT ["python", "-m", "quanthus"]
