FROM python:3.12-slim

WORKDIR /app

# Install pip dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir fastapi uvicorn openai pinecone-client python-dotenv rich tiktoken pydantic-settings langchain langchain-openai langchain-pinecone

# Copy application code
COPY app/ app/

# Railway sets PORT env var
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
