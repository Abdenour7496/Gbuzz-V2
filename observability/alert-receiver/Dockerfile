FROM python:3.12.11-alpine3.22

WORKDIR /app
COPY observability/alert-receiver/server.py /app/server.py

EXPOSE 8080
CMD ["python", "/app/server.py"]
