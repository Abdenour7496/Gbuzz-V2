FROM python:3.12-slim

WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY server.py .

ENV FASTMCP_HOST=0.0.0.0
ENV FASTMCP_PORT=8765
EXPOSE 8765
CMD ["python", "server.py"]
