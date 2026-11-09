FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY q3pipe ./q3pipe
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
ENTRYPOINT ["python", "-m", "q3pipe"]
CMD ["loop"]
