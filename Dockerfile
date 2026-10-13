FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY http_server /app/http_server
COPY httpd.py /app/httpd.py

EXPOSE 80

ENTRYPOINT ["python", "/app/httpd.py", "--config", "/etc/httpd.conf"]
