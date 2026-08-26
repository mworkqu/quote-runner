FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY costing/ ./costing/
COPY agent/ ./agent/
COPY evals/ ./evals/
COPY web/ ./web/
COPY server.py web_api.py ./

# Cloud Run supplies $PORT. Single worker: the agent holds session state in
# memory, so scaling happens by instance, not by worker.
CMD exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
