# settings.py (compatível com Pydantic v2 e tolerante a env)
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator

class Settings(BaseSettings):
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: Optional[str] = None

    embedding_local_path: str = "/models/embeddings/intfloat__multilingual-e5-base"
    embedding_model: str = "intfloat/multilingual-e5-base"
    use_bge: bool = False

    llama_api_base: str = "http://llama:8000/v1"
    llama_api_key: Optional[str] = None
    llama_model_name: str = "local"
    
    rag_token: str = "changeme"
    enable_cors: bool = True
    allowed_origins: List[str] = ["*"]
    enable_rate_limit: bool = True
    rate_limit: str = "5/minute"

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",          # <<-- aceita chaves não declaradas
    )

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, v):
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return ["*"]
        if isinstance(v, str):
            # tenta JSON
            import json
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                # fallback CSV
                return [s.strip() for s in v.split(",") if s.strip()]
        return v

settings = Settings()
