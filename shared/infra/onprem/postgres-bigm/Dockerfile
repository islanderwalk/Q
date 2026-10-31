# syntax=docker/dockerfile:1.7
# PostgreSQL 15（對應 GCP Cloud SQL 的 POSTGRES_15）＋ pg_bigm。
# 官方 postgres 映像沒有 pg_bigm，這裡用兩段式建置：
#   builder：裝與 runtime 同版本的 postgresql-server-dev-15，編譯 pg_bigm
#   runtime：官方映像 + 只複製 pg_bigm 的 .so / .control / .sql
# pg_bigm 原始碼 tarball 由主機端 fetch_pg_bigm.py 下載到 vendor/（sha256 固定），本檔 COPY 進來並再驗一次。
#
# 只在「出口走 TLS 攔截 proxy」的環境需要：
#   build secret `build_ca`（CA bundle）與 build arg https_proxy。沒有 proxy 的環境兩者留空即可。

ARG PG_BASE=mirror.gcr.io/library/postgres:15-bookworm@sha256:daeac543477e189e334f159133f4be6dae739b005cec2dfb9d482509fed05465

FROM ${PG_BASE} AS builder
ARG PG_BIGM_VERSION=1.2-20250903
ARG PG_BIGM_SHA256=4d4fb48161e3f9e99207851299379c83b3e5d1d3c1a07c35f6a3153d577b3142
COPY vendor/pg_bigm-${PG_BIGM_VERSION}.tar.gz /tmp/pg_bigm.tar.gz
RUN --mount=type=secret,id=build_ca,mode=0444,required=false \
    set -eux; \
    echo "${PG_BIGM_SHA256}  /tmp/pg_bigm.tar.gz" | sha256sum -c -; \
    if [ -s /run/secrets/build_ca ]; then \
      sed -i 's|http://|https://|g' /etc/apt/sources.list.d/*; \
      echo 'Acquire::https::CAInfo "/run/secrets/build_ca";' > /etc/apt/apt.conf.d/99-build-ca; \
    fi; \
    apt-get update; \
    # server-dev 版本鎖定成與 runtime 相同的 $PG_VERSION（映像內建的環境變數）
    apt-get install -y --no-install-recommends gcc make libc6-dev "postgresql-server-dev-${PG_MAJOR}=${PG_VERSION}"; \
    mkdir -p /src; tar -xzf /tmp/pg_bigm.tar.gz -C /src --strip-components=1; \
    cd /src; \
    make USE_PGXS=1 PG_CONFIG=/usr/lib/postgresql/${PG_MAJOR}/bin/pg_config; \
    make USE_PGXS=1 PG_CONFIG=/usr/lib/postgresql/${PG_MAJOR}/bin/pg_config install DESTDIR=/out; \
    find /out -type f | sort

FROM ${PG_BASE}
ARG PG_BIGM_VERSION=1.2-20250903
LABEL org.opencontainers.image.title="qsearch-onprem postgres+pg_bigm" \
      qsearch.pg_bigm.version="${PG_BIGM_VERSION}"
COPY --from=builder /out/usr/ /usr/
# pg_bigm 不需要 shared_preload_libraries；CREATE EXTENSION 即可。
