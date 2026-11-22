"""Composition root: builds the provider chain, interpreter, renderer, store and engine."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from .claims.domain import ClaimsDomain
from .claims.interpreter import LLMInterpreter, RuleInterpreter
from .claims.render import TemplateRenderer
from .claims.repository import Repository
from .config import Settings
from .llm.client import LLMClient
from .llm.providers import AnthropicProvider, OpenAICompatProvider
from .sop.engine import Engine

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


@dataclass
class Runtime:
    settings: Settings
    engine: Engine
    store: Any
    repo: Repository
    domain: ClaimsDomain
    llm: LLMClient | None

    @property
    def mode(self) -> str:
        return "model" if self.llm else "rules"


def build_llm(settings: Settings) -> LLMClient | None:
    providers: list[Any] = []
    key = settings.openai_api_key or settings.llm_api_key
    if settings.anthropic_api_key and not settings.llm_base_url:
        model = settings.llm_model or DEFAULT_ANTHROPIC_MODEL
        providers.append(AnthropicProvider(model, settings.anthropic_api_key, settings.llm_effort))
    elif key:
        model = settings.llm_model or DEFAULT_OPENAI_MODEL
        providers.append(
            OpenAICompatProvider(model, key, settings.llm_base_url, settings.llm_extra_body, "primary")
        )
    if settings.llm_fallback_base_url and settings.llm_fallback_model:
        providers.append(
            OpenAICompatProvider(
                settings.llm_fallback_model,
                settings.llm_fallback_api_key or key or "none",
                settings.llm_fallback_base_url,
                settings.llm_extra_body,
                "fallback",
            )
        )
    return LLMClient(providers, max_retries=settings.llm_max_retries) if providers else None


async def open_store(settings: Settings) -> Any:
    url = settings.db_url
    if url.startswith(("postgres://", "postgresql://")):
        from .store.postgres import PostgresStore

        return await PostgresStore(url, settings.session_ttl_seconds).open()
    from .store.sqlite import SQLiteStore

    return await SQLiteStore(url, settings.session_ttl_seconds).open()


def build_runtime(
    settings: Settings,
    store: Any,
    llm: LLMClient | None = None,
    repo: Repository | None = None,
    clock: Callable[[], date] = date.today,
    use_model: bool = True,
) -> Runtime:
    repo = repo or Repository.load(settings.data_dir)
    domain = ClaimsDomain(repo)
    if llm is None and use_model:
        llm = build_llm(settings)
    interpreter: Any = LLMInterpreter(llm, repo) if llm else RuleInterpreter(repo)
    renderer: Any = TemplateRenderer()
    if settings.response_mode == "llm" and llm is not None:
        from .claims.phrasing import LLMRenderer

        renderer = LLMRenderer(llm, repo.documents)
    engine = Engine(domain, interpreter, renderer, store, clock, settings.llm_turn_budget_seconds)
    return Runtime(settings, engine, store, repo, domain, llm)
