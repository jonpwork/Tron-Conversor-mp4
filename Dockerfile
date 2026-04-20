FROM python:3.11-slim

# Instalar ffmpeg (inclui ffprobe)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# --timeout 3600   : worker não morre durante encoding longo
# --workers 1      : 1 worker = sem competição de RAM entre processos
# --worker-class sync : sync é mais estável para tasks pesadas de CPU
# --max-requests 50: recicla worker após 50 requisições (evita leak de memória)
CMD ["gunicorn", "app:app", \
     "--bind", "0.0.0.0:10000", \
     "--workers", "1", \
     "--worker-class", "sync", \
     "--timeout", "3600", \
     "--max-requests", "50", \
     "--max-requests-jitter", "10"]
