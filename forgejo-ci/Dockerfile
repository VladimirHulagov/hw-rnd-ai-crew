FROM python:3.11-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir pytest==8.* httpx pyyaml openpyxl

WORKDIR /app
COPY ci_server.py /app/ci_server.py

EXPOSE 8088
CMD ["python", "-u", "ci_server.py"]
