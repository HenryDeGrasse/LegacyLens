"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """LegacyLens configuration."""

    # OpenAI (used for embeddings — must stay OpenAI to match Pinecone index)
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # LLM provider (OpenRouter or OpenAI-compatible)
    # Set OPENROUTER_API_KEY to route completions through OpenRouter.
    # Falls back to openai_api_key + OpenAI endpoint if not set.
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "gpt-4o-mini"

    # Pinecone
    pinecone_api_key: str = ""
    pinecone_index: str = "spice-fortran"

    # SPICE source
    spice_source_dir: str = "data/spice"

    # Retrieval
    top_k: int = 10
    context_max_tokens: int = 6000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
