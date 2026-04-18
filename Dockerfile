# Usa imagem oficial do Python levinha
FROM python:3.9-slim

# Instala o FFmpeg e dependências essenciais de sistema
RUN apt-get update && apt-get install -y \
    ffmpeg \
    imagemagick \
    && rm -rf /var/lib/apt/lists/*

# Define a pasta de trabalho
WORKDIR /app

# Copia os arquivos do seu repositório
COPY . .

# Instala as bibliotecas (agora sem o Whisper pesado!)
RUN pip install --no-cache-dir -r requirements.txt

# Comando de arranque: 1 worker para não estourar a RAM e timeout de 120s para o vídeo
CMD ["gunicorn", "--workers", "1", "--threads", "1", "--timeout", "120", "--bind", "0.0.0.0:10000", "app:app"]
