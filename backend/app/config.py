"""Central configuration for the FMS v2 API."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- Application ---
    app_name: str = "RMA FMS v2 Chatbot"
    app_env: str = "development"
    app_secret_key: str = "change-this-to-a-strong-random-secret"

    # --- LLM ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # --- FMS v2 LLM Provider Fallback ---
    cerebras_api_key: str = ""
    cerebras_model: str = "gpt-oss-120b"
    groq_api_key: str = ""
    groq_model: str = "gpt-oss-120b"
    nvidia_api_key: str = ""
    nvidia_model: str = ""

    # --- Google Sheets / OAuth ---
    google_sheet_id: str = ""
    google_service_account_json: str = ""

    # --- JWT Auth ---
    jwt_secret_key: str = "change-this-jwt-secret"
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 480

    # --- CORS ---
    allowed_origins: str = "*"

    def validate_production_secrets(self) -> None:
        """Raise SystemExit if app_env != 'development' and default secrets are used."""
        if self.app_env.lower() != "development":
            errors = []
            if self.app_secret_key == "change-this-to-a-strong-random-secret":
                errors.append("APP_SECRET_KEY is still set to its default value.")
            if self.jwt_secret_key == "change-this-jwt-secret":
                errors.append("JWT_SECRET_KEY is still set to its default value.")
            
            if errors:
                import sys
                print("\n" + "="*80)
                print("PRODUCTION CONFIGURATION ERROR")
                for err in errors:
                    print(f"  - {err}")
                print("Please set strong unique values in your production environment variables.")
                print("="*80 + "\n")
                sys.exit(1)

    class Config:
        env_file = (".env", "../.env")
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
