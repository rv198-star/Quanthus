# Build only from the registered CN02 dependency image and the exact Git archive.
# No market data, state, private environment, SSH or OAuth material is build input.
ARG DEPENDENCY_IMAGE
ARG PYTHON_IMAGE
FROM ${DEPENDENCY_IMAGE} AS dependencies
FROM ${PYTHON_IMAGE}
ARG CODE_REVISION
LABEL org.opencontainers.image.source="https://github.com/rv198-star/Quanthus" \
      org.opencontainers.image.revision="${CODE_REVISION}" \
      quanthus.scope="p6-cn02-preactivation"
COPY --from=dependencies /usr/local/lib/python3.10/site-packages/ /usr/local/lib/python3.10/site-packages/
COPY wheels /build/wheels
COPY database.lock /build/database.lock
RUN python -m pip install --no-index --find-links=/build/wheels --require-hashes \
      -r /build/database.lock && rm -rf /build
COPY repository /app
RUN python -c "import pathlib; p=pathlib.Path('/app/config/runtime-code-revision.txt'); p.write_text('${CODE_REVISION}\n')" \
    && chmod -R a-w /app
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp TZ=Asia/Shanghai \
    QUANTHUS_P6_CONTROLLER_ENABLED=0
USER 998:998
WORKDIR /app
ENTRYPOINT ["python", "-m", "quanthus.platform.worker"]
