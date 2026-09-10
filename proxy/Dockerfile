FROM python:3.12-slim

WORKDIR /app
COPY proxy/requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY proxy/main.py .
COPY proxy/request_limits.py .
COPY proxy/egress.py .
COPY proxy/governance_outbox.py .
COPY proxy/governance_service.py .
COPY proxy/access_policy.py .
COPY proxy/nostr_auth.py .
COPY proxy/enterprise_workflows.py .
COPY proxy/document_parsing.py .
COPY proxy/graph_retrieval.py .
COPY proxy/buzz_chat.py .
COPY proxy/buzz_cards.py .
COPY proxy/buzz_wiki.py .
COPY scripts/setup-buzz-knowledge.py ./scripts/setup-buzz-knowledge.py
COPY scripts/check-buzz-knowledge.py ./scripts/check-buzz-knowledge.py
COPY proxy/workspace ./workspace
COPY clients/buzz-knowledge.mjs ./workspace/buzz-knowledge.mjs
COPY scripts/evaluate-knowledge.py ./scripts/evaluate-knowledge.py
COPY schemas ./schemas
COPY tests ./tests

EXPOSE 5001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5001"]
