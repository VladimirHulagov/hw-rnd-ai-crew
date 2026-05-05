FROM debian:13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv build-essential git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/hermes-agent
COPY hermes-agent/ .
RUN pip install --no-cache-dir --break-system-packages ".[all]"

COPY hermes-agent-image/entrypoint.py /opt/entrypoint.py

EXPOSE 8642
ENTRYPOINT ["python3", "/opt/entrypoint.py"]
