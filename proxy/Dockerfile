FROM ghcr.io/xtls/xray-core:latest AS xray
FROM python:3.12-alpine
RUN apk add --no-cache ca-certificates tzdata
COPY --from=xray /usr/local/bin/xray /usr/local/bin/xray
COPY gen_config.py /gen_config.py
COPY entrypoint.py /entrypoint.py
EXPOSE 1080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=12 \
  CMD python3 -c "import socket; s=socket.create_connection(('127.0.0.1',1080),2); s.close()"
ENTRYPOINT ["python3", "/entrypoint.py"]
