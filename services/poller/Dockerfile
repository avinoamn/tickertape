# Build from the repo root so common/ is in the context:
#   docker build -f services/poller/Dockerfile -t tickertape/poller:0.1.0 .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY services/poller/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/ common/
COPY services/poller/main.py services/poller/feeds.yaml ./

RUN useradd --system --uid 10001 poller
USER poller

CMD ["python", "main.py"]
