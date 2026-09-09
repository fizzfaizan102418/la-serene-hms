import unittest

from pydantic import ValidationError

from app.config import Settings


class ProductionConfigurationTests(unittest.TestCase):
    def test_development_allows_sqlite_fallback(self):
        settings = Settings(environment="development", database_url="")
        self.assertEqual(settings.database_url, "")
        self.assertEqual(settings.secret_key, "la-serene-development-secret-change-me")

    def test_production_requires_postgresql_database(self):
        with self.assertRaisesRegex(ValidationError, "HMS_DATABASE_URL is required in production"):
            Settings(environment="production", database_url="", secret_key="x" * 64)

    def test_production_rejects_sqlite_database(self):
        with self.assertRaisesRegex(ValidationError, "must use PostgreSQL"):
            Settings(
                environment="production",
                database_url="sqlite:///la_serene_hms.sqlite3",
                secret_key="x" * 64,
            )

    def test_production_rejects_default_or_short_secret(self):
        with self.assertRaisesRegex(ValidationError, "strong non-default secret"):
            Settings(
                environment="production",
                database_url="postgresql+psycopg://user:pass@127.0.0.1:5432/la_serene_hms",
                secret_key="short",
            )

    def test_production_accepts_valid_configuration(self):
        settings = Settings(
            environment="production",
            database_url="postgresql+psycopg://user:pass@127.0.0.1:5432/la_serene_hms",
            secret_key="x" * 64,
            token_expire_minutes=480,
        )
        self.assertEqual(settings.environment, "production")
        self.assertTrue(settings.database_url.startswith("postgresql+psycopg://"))


if __name__ == "__main__":
    unittest.main()
