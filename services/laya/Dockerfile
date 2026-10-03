# Build from the repo root so common/ is in the context:
#   docker build -f services/laya/Dockerfile -t tickertape/laya:0.1.0 .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    USE_TF=0 \
    HF_HOME=/root/.cache/huggingface
WORKDIR /app

COPY services/laya/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/ common/
COPY services/laya/questions.py services/laya/core.py services/laya/worker.py services/laya/ui.py services/laya/main.py ./

EXPOSE 7860 8000
CMD ["python", "main.py"]
