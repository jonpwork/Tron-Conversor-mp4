# Usa a imagem oficial do Python, bem levinha
FROM python:3.9-slim

# Essa é a mágica: instala o FFmpeg que o conversor de vídeo precisa para não dar erro no Render!
RUN apt-get update && apt-get install -y \
    ffmpeg \
    imagemagick \
    && rm -rf /var/lib/apt/lists/*

# Prepara a pasta do nosso robô
WORKDIR /app

# Copia todos os seus códigos para dentro da máquina do Render
COPY . .

# Instala as bibliotecas do requirements.txt (Flask, Whisper, Moviepy...)
RUN pip install --no-cache-dir -r requirements.txt

# Liga o motor principal!
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "app:app"]

