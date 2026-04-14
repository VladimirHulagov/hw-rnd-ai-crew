FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt

COPY requirements.txt /opt/requirements.txt
RUN pip install --no-cache-dir -r /opt/requirements.txt

COPY supervisord.conf /etc/supervisor/supervisord.conf
COPY orchestrator/ /opt/orchestrator/
COPY config-template.yaml /opt/config-template.yaml

RUN mkdir -p /paperclip/hermes-instances /run/gateway-ports /var/log/supervisor /etc/supervisor/conf.d

EXPOSE 8642-8673

ENTRYPOINT ["/usr/local/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
