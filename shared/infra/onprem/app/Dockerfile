# syntax=docker/dockerfile:1.7
# scheduler / worker / parser / replay / smoke test 共用的映像。
# 對應 GCP 的 scheduler、crawler-worker、parser 三個 Cloud Run 服務（地端合成一個映像、不同 command）。
ARG PY_BASE=mirror.gcr.io/library/python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
FROM ${PY_BASE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt constraints.txt /app/
# build_ca 只在 TLS 攔截 proxy 的環境需要；不會寫進映像層。
RUN --mount=type=secret,id=build_ca,mode=0444,required=false \
    set -eux; \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip install --no-cache-dir -r requirements.txt -c constraints.txt; \
    pip freeze > /app/pip-freeze.txt

COPY qsearch_onprem /app/qsearch_onprem
COPY tests /app/tests
RUN useradd --system --uid 10001 --home /app qsearch
USER 10001
CMD ["python", "-m", "qsearch_onprem.worker"]
