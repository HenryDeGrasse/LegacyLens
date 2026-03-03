"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """LegacyLens configuration."""

    # OpenAI
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    llm_model: str = "gpt-4o-mini"

    # Pinecone
    pinecone_api_key: str = ""
    pinecone_index: str = "spice-fortran"

    # SPICE source
    spice_source_dir: str = "data/spice"

    # Retrieval
    top_k: int = 10
    context_max_tokens: int = 4500

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
