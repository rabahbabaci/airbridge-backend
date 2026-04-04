import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    app_name: str = os.getenv("APP_NAME", "airbridge-backend")
    app_version: str = "0.1.0"
    app_env: str = os.getenv("APP_ENV", "development")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    rapidapi_key: str = os.getenv("RAPIDAPI_KEY", "")
    google_maps_api_key: str = os.getenv("GOOGLE_MAPS_API_KEY", "")
    database_url: str = os.getenv("DATABASE_URL", "")
    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_key: str = os.getenv("SUPABASE_KEY", "")
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-secret-change-me")
    firebase_credentials_json: str = os.getenv("FIREBASE_CREDENTIALS_JSON", "")
    sentry_dsn: str = os.getenv("SENTRY_DSN", "")
    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    stripe_price_monthly: str = os.getenv("STRIPE_PRICE_MONTHLY", "")
    stripe_price_annual: str = os.getenv("STRIPE_PRICE_ANNUAL", "")


settings = Settings()
