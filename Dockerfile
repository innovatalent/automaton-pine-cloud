FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 ENGINE_TG=0 AUTOMATON_DASHBOARD_UI_ONLY=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/public /app/logs && chown -R 10001:10001 /app
USER 10001
EXPOSE 10000
CMD ["python", "-u", "cloud_server.py"]
