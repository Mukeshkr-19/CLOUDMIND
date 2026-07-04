FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY *.py ./
RUN pip install --no-cache-dir requests docker prometheus_client flask waitress
ENV PYTHONUNBUFFERED=1
CMD ["python","-u","/app/watcher.py"]
