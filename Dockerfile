FROM python:3.12-slim

WORKDIR /app

# Create non-root user for security
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --create-home appuser

# Install pip dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy everything in one layer to avoid stale caches
COPY . .

# Set ownership and switch to non-root
RUN chown -R appuser:appuser /app
USER appuser

# Railway sets PORT env var
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
