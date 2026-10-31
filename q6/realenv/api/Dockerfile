# 文字雲查詢 API（Cloud Run 服務）＋ PM A/B 前端靜態頁。建置 context = q6/realenv/
# 建置環境若有 TLS 攔截代理，用 --secret id=ca,src=<CA 檔> 傳入（只在建置時掛載，不進映像）
FROM mirror.gcr.io/library/python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir fastapi==0.162.0 uvicorn==0.54.0 requests==2.34.2 prometheus-client==0.26.0
WORKDIR /app
COPY api/app.py /app/app.py
COPY web/ /app/web/
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
