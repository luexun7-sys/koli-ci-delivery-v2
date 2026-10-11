FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 KOLI_DB=/data/koli.db
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gosu && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py admin_api.py ./
COPY static/ ./static/
COPY docker-entrypoint.sh /usr/local/bin/koli-entrypoint
RUN useradd --create-home --uid 10001 koli && mkdir -p /data && chown -R koli:koli /data /app && chmod 755 /usr/local/bin/koli-entrypoint
EXPOSE 8000
ENTRYPOINT ["koli-entrypoint"]
CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]

