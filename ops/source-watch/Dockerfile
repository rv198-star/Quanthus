# Build context: verified Git source bundle, hash-locked runtime wheels and lock file.
# No network is needed during the build. Pass the full published Git revision.
FROM python:3.10-slim-bookworm@sha256:db6ee5170ebce0f4d165af242e8e77addc08bd9ffc37e8b1815752cf027aa4f8
ARG QUANTHUS_REVISION
ARG P10_POLICY=config/pipelines/p10-supplement-sources.v1.json
LABEL org.opencontainers.image.source="https://github.com/rv198-star/Quanthus"
LABEL org.opencontainers.image.revision="${QUANTHUS_REVISION}"
ENV PYTHONPATH=/opt/quanthus/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY wheels /tmp/wheels
COPY requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-index --find-links=/tmp/wheels --require-hashes \
      -r /tmp/requirements.lock && rm -rf /tmp/wheels /tmp/requirements.lock
COPY code /opt/quanthus
RUN python -c 'import sys; from pathlib import Path; from quanthus.pipelines.securities_warehouse.container_code import verify_bundle; verify_bundle(Path("/opt/quanthus"), sys.argv[1])' "$QUANTHUS_REVISION"
RUN python -c 'import sys; from pathlib import Path; from quanthus.p10_source_contracts import library_contracts, policy; library_contracts(policy(Path("/opt/quanthus") / sys.argv[1]))' "$P10_POLICY"
USER 998:998
WORKDIR /opt/quanthus
ENTRYPOINT ["python", "-m", "quanthus.p10_supplement"]
