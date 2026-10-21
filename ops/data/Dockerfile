ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE} AS dependencies
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY runtime.lock /build/runtime.lock
RUN python -m pip install --only-binary=:all: --require-hashes \
    --prefix=/runtime -r /build/runtime.lock

FROM ${PYTHON_IMAGE} AS pipeline
ARG DATA_UID=998
ARG DATA_GID=998
ARG PIPELINE_REVISION
LABEL org.opencontainers.image.source="https://github.com/rv198-star/Quanthus" \
      org.opencontainers.image.revision="${PIPELINE_REVISION}"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/pipeline/src HOME=/tmp TZ=Asia/Shanghai
COPY --from=dependencies /runtime/ /usr/local/
COPY pipeline /opt/pipeline
COPY collector /opt/collector
RUN python -c "from pathlib import Path; from quanthus.pipelines.securities_warehouse.container_code import verify_bundle; from quanthus.pipelines.securities_warehouse.daily_base24_provider import COLLECTOR; verify_bundle(Path('/opt/pipeline'), '${PIPELINE_REVISION}'); verify_bundle(Path('/opt/collector'), COLLECTOR)" \
    && chmod -R a-w /opt/pipeline /opt/collector
USER ${DATA_UID}:${DATA_GID}
WORKDIR /opt/pipeline
ENTRYPOINT ["python", "-m", "quanthus.pipelines.securities_warehouse.container_jobs"]
CMD ["--help"]
