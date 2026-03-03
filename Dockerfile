FROM python:3.12-slim

WORKDIR /app

# Install pip dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ app/

# Copy call graph data for dependency/impact features
COPY data/call_graph.json data/call_graph.json

# Railway sets PORT env var
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
