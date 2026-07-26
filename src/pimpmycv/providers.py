from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, Literal

from openai import AzureOpenAI, OpenAI

# Optional dotenv support for .env files
try:
    from dotenv import load_dotenv
    _has_dotenv = True
except ImportError:
    _has_dotenv = False


ProviderName = Literal["openai", "azure", "ollama"]
PROVIDERS: tuple[ProviderName, ...] = ("openai", "azure", "ollama")


class ProviderConfigError(ValueError):
    """Raised when the selected model provider is not configured."""

# Load .env file if it exists and dotenv is available
if _has_dotenv:
    load_dotenv()


@dataclass(frozen=True)
class Backend:
    provider: ProviderName
    client: Any
    model: str
    supports_stateful_responses: bool = True
    response_options: dict[str, Any] = field(default_factory=dict)


def _with_v1(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    if not endpoint.endswith("/v1"):
        endpoint += "/v1"
    return endpoint + "/"


def create_backend(
    provider: ProviderName,
    *,
    model: str | None = None,
    endpoint: str | None = None,
) -> Backend:
    """Create an OpenAI-SDK client configured for the chosen endpoint.
    
    Environment variables can be set directly or loaded from a .env file.
    For Azure, the following variables are required:
    - AZURE_OPENAI_API_KEY
    - AZURE_OPENAI_ENDPOINT
    - AZURE_OPENAI_DEPLOYMENT (or --model)
    - AZURE_OPENAI_API_VERSION or OPENAI_API_VERSION
    """
    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ProviderConfigError("Set OPENAI_API_KEY for the OpenAI provider.")
        base_url = endpoint or os.getenv("OPENAI_BASE_URL")
        client_options: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_options["base_url"] = _with_v1(base_url)
        return Backend(
            provider="openai",
            client=OpenAI(**client_options),
            model=model or "gpt-5.6-sol",
            response_options={
                "reasoning": {"effort": "medium"},
                "tool_choice": "required",
                "parallel_tool_calls": False,
            },
        )

    if provider == "azure":
        api_key = os.getenv("AZURE_OPENAI_API_KEY")
        azure_endpoint = endpoint or os.getenv("AZURE_OPENAI_ENDPOINT")
        deployment = model or os.getenv("AZURE_OPENAI_DEPLOYMENT")
        api_version = os.getenv("AZURE_OPENAI_API_VERSION") or os.getenv(
            "OPENAI_API_VERSION"
        )
        missing = [
            name
            for name, value in (
                ("AZURE_OPENAI_API_KEY", api_key),
                ("AZURE_OPENAI_ENDPOINT", azure_endpoint),
                ("--model or AZURE_OPENAI_DEPLOYMENT", deployment),
                (
                    "AZURE_OPENAI_API_VERSION or OPENAI_API_VERSION",
                    api_version,
                ),
            )
            if not value
        ]
        if missing:
            raise ProviderConfigError(
                "Missing Azure OpenAI configuration: " + ", ".join(missing) + "."
            )
        return Backend(
            provider="azure",
            client=AzureOpenAI(
                api_key=api_key,
                azure_endpoint=azure_endpoint,
                api_version=api_version,
            ),
            model=deployment,
            response_options={
                "tool_choice": "required",
                "parallel_tool_calls": False,
            },
        )

    if provider == "ollama":
        base_url = endpoint or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        api_key = os.getenv("OLLAMA_API_KEY", "ollama")
        return Backend(
            provider="ollama",
            client=OpenAI(api_key=api_key, base_url=_with_v1(base_url)),
            model=model or os.getenv("OLLAMA_MODEL", "qwen3:8b"),
            supports_stateful_responses=False,
            # Ollama supports tools but not these optional Responses controls.
            response_options={},
        )

    raise ProviderConfigError(f"Unknown provider: {provider!r}.")
