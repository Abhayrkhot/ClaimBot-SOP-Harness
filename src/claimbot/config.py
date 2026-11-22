"""Runtime configuration from environment variables (names kept compatible with v1 deployments)."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Primary model provider: OpenAI-compatible (OpenAI, vLLM, Ollama, ...) or Anthropic.
    openai_api_key: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    anthropic_api_key: str | None = None
    llm_effort: str | None = Field(default=None, description="Anthropic output_config.effort")
    llm_extra_body: dict[str, Any] = Field(
        default_factory=dict, description="JSON merged into OpenAI-compatible requests"
    )

    # Optional fallback provider (OpenAI-compatible) used when the primary fails or its breaker is open.
    llm_fallback_base_url: str | None = None
    llm_fallback_model: str | None = None
    llm_fallback_api_key: str | None = None

    llm_turn_budget_seconds: float = 45.0
    llm_max_retries: int = 2
    response_mode: Literal["template", "llm"] = "template"
    """``llm`` phrases replies with the model and checks them with the grounding verifier."""

    session_db: str = ":memory:"
    """SQLite path, or a ``postgresql://`` URL."""
    database_url: str | None = None
    session_ttl_seconds: int = 1800
    data_dir: str | None = None

    admin_api_token: str | None = None
    rate_limit_per_minute: int = 60
    trust_forwarded_for: bool = False

    smtp_host: str | None = None
    smtp_port: int = 1025
    smtp_sender: str = "claims-bot@example.com"
    outbox_poll_seconds: float = 2.0

    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"
    otel_exporter_otlp_endpoint: str | None = None

    @field_validator("llm_extra_body", mode="before")
    @classmethod
    def _parse_extra(cls, value: Any) -> Any:
        if isinstance(value, str):
            return json.loads(value) if value.strip() else {}
        return value

    @property
    def db_url(self) -> str:
        return self.database_url or self.session_db

    @property
    def model_configured(self) -> bool:
        return bool(self.anthropic_api_key or self.openai_api_key or self.llm_api_key)
