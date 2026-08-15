FROM python:3.12-slim

WORKDIR /app
COPY graphiti-projector/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY graphiti-projector/main.py .
COPY graphiti-projector/test_main.py .

CMD ["python", "main.py"]
