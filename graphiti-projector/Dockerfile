FROM python:3.12-slim

WORKDIR /app
COPY graphiti-projector/requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY graphiti-projector/main.py .
COPY graphiti-projector/control.py .
COPY graphiti-projector/test_main.py .

CMD ["python", "main.py"]
