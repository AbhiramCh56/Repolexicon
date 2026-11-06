from pathlib import Path

from pydantic_settings import BaseSettings

# backend/ - resolved from this file so paths never depend on the working directory
BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    PROJECT_NAME: str = "RepoMind AI"

    # Database
    DATABASE_URL: str

    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: float = 15.0
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS
    FRONTEND_URL: str = "http://localhost:5173"

    ENVIRONMENT: str = "development"

    # Feature gate for the LLM chat endpoints. Set CHAT_ENABLED=false in
    # production to take chat offline (503 on the ask endpoints) without
    # removing the routes or the chat history. Re-enable once hardened.
    CHAT_ENABLED: bool = True

    # In-process auth rate limiting (see app/core/rate_limit.py). Counters live
    # in one worker's memory, so scaling past a single worker or instance means
    # moving the store to something shared (Redis). Set false to disable.
    RATE_LIMIT_ENABLED: bool = True
    # Hard cap on tracked keys so distinct spoofed IPs/usernames cannot grow the
    # store without bound.
    RATE_LIMIT_MAX_KEYS: int = 10_000
    # Login: many attempts from one address, and few on one account from any.
    LOGIN_IP_LIMIT: int = 20
    LOGIN_IP_WINDOW_SECONDS: int = 300
    LOGIN_ACCOUNT_LIMIT: int = 5
    LOGIN_ACCOUNT_WINDOW_SECONDS: int = 900
    # Registration is rare and cheap; keep it strict.
    REGISTER_IP_LIMIT: int = 5
    REGISTER_IP_WINDOW_SECONDS: int = 3600
    # Refresh is called automatically, so the budget is generous.
    REFRESH_IP_LIMIT: int = 60
    REFRESH_IP_WINDOW_SECONDS: int = 3600

    # Header that carries the true client address when a proxy fronts the app.
    # On Render all traffic arrives through Cloudflare, which sets
    # CF-Connecting-IP to the real client and overwrites anything a client
    # sends, so set CLIENT_IP_HEADER=cf-connecting-ip there. Leave unset when
    # the app is directly reachable (local dev), where request.client is the
    # real peer. Only point this at a header the edge controls; x-forwarded-for
    # is client-forgeable. If it is unset behind a proxy, every request reports
    # the proxy, all clients share one per-IP bucket, and a few failed logins
    # lock out everyone.
    CLIENT_IP_HEADER: str | None = None

    # LLM API keys
    OPENAI_API_KEY: str | None = None
    NVIDIA_API_KEY: str | None = None
    GROQ_API_KEY: str | None = None

    # Embedding API keys (separate from the chat LLM; chat still uses Groq).
    GEMINI_API_KEY: str | None = None
    HF_TOKEN: str | None = None

    # Storage (defaults live under backend/.data so they are CWD-independent)
    CHROMA_STORAGE_DIR: str = str(BACKEND_ROOT / ".data" / "chroma")
    REPO_STORAGE_DIR: str = str(BACKEND_ROOT / ".data" / "repos")
    # Rendered manifests, written as a convenience alongside the database row.
    MANIFEST_STORAGE_DIR: str = str(BACKEND_ROOT / ".data" / "manifests")

    # Parsing / embedding
    # "gemini" uses the Gemini Developer API (requires GEMINI_API_KEY),
    # "huggingface" uses the Hugging Face Inference API (requires HF_TOKEN),
    # and "local" loads the model in-process with sentence-transformers.
    EMBEDDING_BACKEND: str = "gemini"
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-2"
    # Gemini supports 128-3072 dimensions. 1536 keeps storage reasonable while
    # preserving most of the model's retrieval quality.
    GEMINI_EMBEDDING_DIMENSIONS: int = 1536
    # Model for the huggingface / local backends.
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    # Inference provider on Hugging Face. hf-inference serves MiniLM; leave
    # blank to let huggingface_hub pick automatically.
    HF_INFERENCE_PROVIDER: str = "hf-inference"
    EMBEDDING_BATCH_SIZE: int = 100
    CHUNK_SIZE_CHARS: int = 1500
    MAX_FILE_SIZE_BYTES: int = 5 * 1024 * 1024

    # LLM
    LLM_PROVIDER: str = "groq"
    LLM_MODEL_NVIDIA: str = "nvidia/nemotron-3.5-lightning-30b-a3b"
    LLM_MODEL_GROQ: str = "openai/gpt-oss-20b"
    LLM_TEMPERATURE: float = 0.2

    class Config:
        env_file = str(BACKEND_ROOT / ".env")

settings = Settings()
