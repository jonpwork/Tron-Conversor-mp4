FROM python:3.9-slim
RUN apt-get update && apt-get install -y ffmpeg imagemagick && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
CMD gunicorn --workers 1 --threads 1 --timeout 120 --bind 0.0.0.0:$PORT app:app
