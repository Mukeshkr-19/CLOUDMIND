FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY watcher.py .
RUN pip install --no-cache-dir requests docker prometheus_client
ENV PYTHONUNBUFFERED=1
CMD ["python","-u","/app/watcher.py"]
