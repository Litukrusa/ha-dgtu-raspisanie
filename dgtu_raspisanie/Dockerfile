FROM ghcr.io/home-assistant/base-python:3.14-alpine3.24

COPY app /app
COPY rootfs /

RUN chmod a+x /etc/services.d/dgtu/run /etc/services.d/dgtu/finish
