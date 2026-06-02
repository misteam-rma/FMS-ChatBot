"""
Botivate HR Support - Central Configuration
All values are loaded from environment variables / .env file.
Nothing is hardcoded.
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # --- Application ---
    app_name: str = "Botivate HR Support"
    app_env: str = "development"
    app_secret_key: str = "change-this-to-a-strong-random-secret"
    app_base_url: str = "http://localhost:8000"

    # --- Master Database ---
    database_url: str = "sqlite+aiosqlite:///./botivate_master.db"

    # --- LLM ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # --- Google Sheets / OAuth ---
    google_service_account_json: str = ""
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = "http://localhost:5173/oauth/callback"

    # --- SMTP Email ---
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_use_tls: bool = True
    smtp_user: str = ""
    smtp_password: str = ""

    # --- JWT Auth ---
    jwt_secret_key: str = "change-this-jwt-secret"
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 480

    # --- ChromaDB ---
    chroma_persist_dir: str = "./chroma_data"

    # --- Upload Directories ---
    upload_dir: str = "./uploads"

    # --- Development/Demo Mode ---
    skip_google_auth: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
