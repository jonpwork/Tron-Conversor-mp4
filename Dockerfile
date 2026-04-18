FROM python:3.9-slim

# Instala motores de vídeo
RUN apt-get update && apt-get install -y ffmpeg imagemagick && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# Instala as bibliotecas leves (Flask, Groq...)
RUN pip install --no-cache-dir -r requirements.txt

# O PULO DO GATO: Comando sem aspas para o Render injetar a porta certa
CMD gunicorn --workers 1 --threads 1 --timeout 120 --bind 0.0.0.0:$PORT app:app
