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
    enable_polling_agent: bool = os.getenv("ENABLE_POLLING_AGENT", "true").lower() in ("true", "1", "yes")
    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_key: str = os.getenv("SUPABASE_KEY", "")
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-secret-change-me")
    firebase_credentials_json: str = os.getenv("FIREBASE_CREDENTIALS_JSON", "")
    sentry_dsn: str = os.getenv("SENTRY_DSN", "")
    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    stripe_price_monthly: str = os.getenv("STRIPE_PRICE_MONTHLY", "")
    stripe_price_annual: str = os.getenv("STRIPE_PRICE_ANNUAL", "")
    tsa_wait_times_api_key: str = os.getenv("TSA_WAIT_TIMES_API_KEY", "")
    sendgrid_api_key: str = os.getenv("SENDGRID_API_KEY", "")
    from_email: str = os.getenv("FROM_EMAIL", "noreply@airbridge.com")
    twilio_account_sid: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    twilio_from_number: str = os.getenv("TWILIO_FROM_NUMBER", "")


settings = Settings()
