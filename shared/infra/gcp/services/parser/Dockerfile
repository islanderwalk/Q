FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py /app/main.py
ENV PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --no-create-home app
USER 10001
# exec: uvicorn replaces sh as PID 1, so Cloud Run's SIGTERM reaches it (graceful shutdown).
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
