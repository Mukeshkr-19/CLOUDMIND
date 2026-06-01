# 1. Base image
FROM python:3.12-slim

# 2. Set working directory inside container
WORKDIR /app

# 3. Copy your code into the container
COPY service.py .

# 4. Install Flask (our only dependency)
RUN pip install flask
RUN pip install prometheus_client psutil
# 5. Expose the port Flask runs on
EXPOSE 5050

# 6. Command to start the service
CMD ["python", "service.py"]
