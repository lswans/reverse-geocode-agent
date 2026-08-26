FROM public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 AS lambda-adapter
FROM python:3.12.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app
COPY --from=lambda-adapter /lambda-adapter /opt/extensions/lambda-adapter
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements.txt
COPY server.py agent.py agent_stub.py ./
COPY static/ ./static/

EXPOSE 8080
CMD ["python", "server.py"]
