# 斷詞映像：回補 Job（Cloud Run Jobs）與串流服務（Cloud Run 服務）共用這一個映像，差別只在啟動參數。
# 建置 context = q6/（要帶入 q6/code/segmenter.py，與 repo 原型同一份前處理與過濾規則）。
# 建置環境若有 TLS 攔截代理，用 --secret id=ca,src=<CA 檔> 傳入（只在建置時掛載，不進映像）
FROM mirror.gcr.io/library/python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir jieba==0.42.1 requests==2.34.2 google-cloud-pubsub==2.43.0 \
    prometheus-client==0.26.0 "psycopg[binary]==3.3.6"
WORKDIR /app
COPY code/segmenter.py /app/segmenter.py
COPY code/dict/ /app/dict/
COPY stack/seg/qseg/ /app/qseg/
COPY stack/seg/quality/ /app/quality/
COPY stack/pg/live.sql /app/live.sql
ARG SEG_BUILD=dev
ENV SEG_BUILD=${SEG_BUILD}
ENTRYPOINT ["python", "-m", "qseg"]
