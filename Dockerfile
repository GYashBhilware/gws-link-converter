FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY sql ./sql
COPY tools ./tools
RUN useradd --uid 10001 --no-create-home appuser
USER appuser
# Service: the web app. The sync Cloud Run job overrides the command with
# `python -m tools.sync_job`.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --no-server-header"]
