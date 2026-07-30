# 1. Base image
FROM python:3.12-slim

# 2. Set working directory inside container
WORKDIR /app

# 3. Copy your code into the container
COPY service.py .

# 4. Install runtime dependencies
RUN pip install --no-cache-dir flask prometheus_client psutil requests==2.34.2
# 5. Expose the port Flask runs on
EXPOSE 5051

# 6. Command to start the service
CMD ["python", "service.py"]
