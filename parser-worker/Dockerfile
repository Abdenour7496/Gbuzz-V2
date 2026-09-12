FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
WORKDIR /app
COPY proxy/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock && useradd --system --uid 10001 parser
COPY proxy/document_parsing.py /app/document_parsing.py
COPY parser-worker/worker.py /app/worker.py
USER 10001:10001
ENTRYPOINT ["python", "/app/worker.py"]
