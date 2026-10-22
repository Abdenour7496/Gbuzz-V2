# S3-compatible object store for CI integration only. The pinned MinIO image is no longer
# publicly pullable from quay.io (unauthorized), so CI builds a moto S3 server from PyPI instead.
# Production keeps MinIO; see docs/production-blockers-2026-10-22.md (image availability).
FROM python:3.12-slim
RUN pip install --no-cache-dir "moto[server]==5.2.4"
EXPOSE 9000
CMD ["moto_server", "-H", "0.0.0.0", "-p", "9000"]
