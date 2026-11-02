FROM python:3.13-slim
WORKDIR /app
COPY probe_server.py .
USER 10001
EXPOSE 10000
CMD ["python", "-u", "probe_server.py"]
