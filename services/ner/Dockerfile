# Build from the repo root so common/ and the poller's feeds.yaml (watchlist) are in the context:
#   docker build -f services/ner/Dockerfile -t tickertape/ner:0.1.0 .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/root/.cache/huggingface
WORKDIR /app

COPY services/ner/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/ common/
COPY services/poller/feeds.yaml feeds.yaml
COPY services/ner/company_tickers.json services/ner/core.py services/ner/worker.py services/ner/ui.py services/ner/main.py ./

EXPOSE 7860 8000
CMD ["python", "main.py"]
