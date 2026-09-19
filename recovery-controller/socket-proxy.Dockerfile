FROM python:3.12-slim
WORKDIR /app
COPY recovery-controller/main.py recovery-controller/socket_proxy.py ./
CMD ["python", "socket_proxy.py"]
