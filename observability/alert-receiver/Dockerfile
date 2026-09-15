FROM python:3.12.11-alpine3.22

WORKDIR /app
COPY proxy/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.lock
COPY observability/alert-receiver/server.py /app/server.py

EXPOSE 8080
CMD ["python", "/app/server.py"]
