"""Ajustes del proyecto leídos de `.env` (los secretos nunca se imprimen ni se guardan)."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """`ENTSOE_API_KEY` es un `SecretStr`: su `repr` es `**********`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    entsoe_api_key: SecretStr | None = None


def get_settings() -> Settings:
    return Settings()
