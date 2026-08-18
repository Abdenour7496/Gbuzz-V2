FROM python:3.12-slim

WORKDIR /app
COPY projector/requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY projector/main.py .
COPY projector/test_main.py .

CMD ["python", "main.py"]
