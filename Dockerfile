# Imagen unica para la API/web, el motor de alertas y la semilla de demo.
# Los recolectores (snmp/syslog/ipfix) NO van aqui: necesitan rsyslog, nfcapd y UDP del host (ver docs/docker.md).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Sin root: la BD vive en el volumen /data
RUN useradd --system --create-home --uid 10001 noc \
    && mkdir -p /data \
    && chown noc:noc /data
USER noc

ENV MININOC_DB_PATH=/data/mini_noc.db \
    MININOC_API_HOST=0.0.0.0 \
    MININOC_API_PORT=8080

EXPOSE 8080
CMD ["python", "-m", "api"]
