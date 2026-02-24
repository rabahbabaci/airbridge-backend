import os


class Settings:
    app_name: str = os.getenv("APP_NAME", "airbridge-backend")
    app_version: str = "0.1.0"
    app_env: str = os.getenv("APP_ENV", "development")
    app_port: int = int(os.getenv("APP_PORT", "8000"))


settings = Settings()
