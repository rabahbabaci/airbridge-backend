"""Verify test DB isolation: services that bypass get_db see None factory."""

import os


class TestDBIsolation:
    def test_async_session_factory_is_none_during_tests(self):
        """The module-level async_session_factory must be None in tests.

        Services like trip_intake, polling_agent, airport_cache, and the
        subscriptions webhook import app.db and use app.db.async_session_factory
        directly, bypassing get_db. If the factory isn't None, these services
        would connect to whatever DATABASE_URL is configured — which was
        production Supabase for 15+ days.
        """
        import app.db as _db

        assert _db.async_session_factory is None, (
            "async_session_factory must be None during tests to prevent "
            "services from bypassing get_db and hitting a real database. "
            f"Got: {_db.async_session_factory}"
        )

    def test_trip_intake_sees_none_factory(self):
        """trip_intake._db_available() must return False during tests."""
        from app.services.trip_intake import _db_available

        assert _db_available() is False, (
            "_db_available() returned True, meaning trip_intake would write "
            "to a real database during tests."
        )

    def test_database_url_is_not_production(self):
        """DATABASE_URL must not point at production."""
        db_url = os.environ.get("DATABASE_URL", "")
        assert "supabase" not in db_url.lower(), f"DATABASE_URL points at Supabase: {db_url}"
        assert "railway" not in db_url.lower(), f"DATABASE_URL points at Railway: {db_url}"

    def test_sentry_dsn_is_empty(self):
        """SENTRY_DSN must be empty during tests to prevent false alerts."""
        assert os.environ.get("SENTRY_DSN", "") == "", (
            f"SENTRY_DSN is set: {os.environ.get('SENTRY_DSN')}"
        )

    def test_polling_agent_disabled(self):
        """ENABLE_POLLING_AGENT must be false during tests."""
        from app.core.config import settings

        assert settings.enable_polling_agent is False, (
            "Polling agent is enabled during tests. Set ENABLE_POLLING_AGENT=false in .env.test."
        )
