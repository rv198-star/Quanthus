FROM python:3.11-slim

ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN python -m pip install --no-cache-dir \
    "cryptography>=45,<46" \
    "gunicorn>=26,<27" \
    "psycopg[binary]>=3.2,<4" \
    "pytest>=8.3,<9"

WORKDIR /workspace
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/platform/test_postgres_acceptance.py"]
