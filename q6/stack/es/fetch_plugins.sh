#!/usr/bin/env bash
# 下載三個外掛並驗證。smartcn、ICU 比對 Elastic 官方 .sha512；IK 沒有官方雜湊檔，記錄下載當下的 sha512。
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p plugins && cd plugins
V=9.5.5
for p in analysis-smartcn analysis-icu; do
  curl -sSLO "https://artifacts.elastic.co/downloads/elasticsearch-plugins/$p/$p-$V.zip"
  curl -sSLO "https://artifacts.elastic.co/downloads/elasticsearch-plugins/$p/$p-$V.zip.sha512"
  sha512sum -c "$p-$V.zip.sha512"
done
curl -sSLO "https://release.infinilabs.com/analysis-ik/stable/elasticsearch-analysis-ik-$V.zip"
sha512sum "elasticsearch-analysis-ik-$V.zip"
