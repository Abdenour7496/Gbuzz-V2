FROM python:3.12-slim

WORKDIR /app
COPY proxy/requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY proxy/main.py .
COPY schemas ./schemas
COPY tests ./tests

EXPOSE 5001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5001"]
