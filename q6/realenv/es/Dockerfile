# 官方 Elasticsearch 9.5.5 映像 + 三個中文分析外掛（smartcn、ICU 為 Elastic 官方；IK 為 INFINI Labs 維護）。
# 外掛 zip 由 fetch_plugins.sh 下載並比對 sha512，再從本機檔案安裝（建置時不連外）。
FROM mirror.gcr.io/library/elasticsearch:9.5.5
COPY --chown=1000:0 plugins/ /tmp/plugins/
RUN bin/elasticsearch-plugin install --batch file:///tmp/plugins/analysis-smartcn-9.5.5.zip \
 && bin/elasticsearch-plugin install --batch file:///tmp/plugins/analysis-icu-9.5.5.zip \
 && bin/elasticsearch-plugin install --batch file:///tmp/plugins/elasticsearch-analysis-ik-9.5.5.zip \
 && rm -rf /tmp/plugins
# IK 設定：遠端字典指向字典 artifact 伺服器（GCS 的本機替身），每 60 秒 HEAD 一次，Last-Modified／ETag 變了就重載
COPY --chown=1000:0 ik/IKAnalyzer.cfg.xml /usr/share/elasticsearch/config/analysis-ik/IKAnalyzer.cfg.xml
# 快照倉庫目錄（docker-compose 的 essnap 具名卷掛在這裡）：先在映像裡建好並給 elasticsearch 使用者，
# 具名卷第一次建立時會繼承這裡的擁有者；否則卷是 root 擁有，ES 建 fs 倉庫會報 failed to create blob container
RUN mkdir -p /usr/share/elasticsearch/snapshots && chown 1000:0 /usr/share/elasticsearch/snapshots
