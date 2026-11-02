FROM python:3.10-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY workbench/requirements*.txt /app/workbench/
RUN pip install --no-cache-dir -r /app/workbench/requirements-sdpc.txt
COPY workbench /app/workbench
COPY README.md DEPLOYMENT.md /app/
EXPOSE 8766
CMD ["python", "-m", "workbench.server", "--host", "0.0.0.0", "--port", "8766"]
