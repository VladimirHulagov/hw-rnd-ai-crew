FROM python:3.11-slim

WORKDIR /app
COPY agent-operator/ .
RUN pip install --no-cache-dir fastapi uvicorn psycopg2-binary kubernetes

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]
