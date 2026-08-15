FROM python:3.12-slim

WORKDIR /app
COPY proxy/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY proxy/main.py .
COPY schemas ./schemas
COPY tests ./tests

EXPOSE 5001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5001"]
