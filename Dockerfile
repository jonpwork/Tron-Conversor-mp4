# Instala as bibliotecas leves
RUN pip install --no-cache-dir -r requirements.txt

# O comando que o Render ama
CMD gunicorn --workers 1 --threads 1 --timeout 120 --bind 0.0.0.0:$PORT app:app
