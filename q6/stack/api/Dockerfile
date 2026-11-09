# 文字雲查詢 API（Cloud Run 服務）＋ PM A/B 前端靜態頁＋公開 feed 採集器（同一個映像，command 不同）。建置 context = q6/stack/
# 映像裡帶著 data/channels 的 267 篇六渠道樣本，給 PM 頁「重播樣本」用。
# 建置環境若有 TLS 攔截代理，用 --secret id=ca,src=<CA 檔> 傳入（只在建置時掛載，不進映像）
FROM mirror.gcr.io/library/python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir fastapi==0.162.0 uvicorn==0.54.0 requests==2.34.2 prometheus-client==0.26.0 \
    "psycopg[binary,pool]==3.3.6" google-cloud-pubsub==2.43.0
WORKDIR /app
COPY api/app.py api/ops.py api/collector.py api/feeds.json /app/
COPY pg/live.sql /app/live.sql
COPY web/ /app/web/
COPY data/channels/ /app/data/channels/
EXPOSE 8080
# keep-alive 75 秒（uvicorn 預設 5 秒）：用戶端重用閒置超過 5 秒的連線時，伺服器若剛好關掉，那次請求會變成連線中斷
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--timeout-keep-alive", "75"]
