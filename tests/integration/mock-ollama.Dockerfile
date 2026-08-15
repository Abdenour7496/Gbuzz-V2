FROM python:3.12-slim
WORKDIR /app
COPY tests/integration/mock_ollama.py .
EXPOSE 11434
CMD ["python", "mock_ollama.py"]
