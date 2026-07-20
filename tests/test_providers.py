import pytest

from pimpmycv import providers


def _fake_openai(monkeypatch):
    clients = []

    def factory(**kwargs):
        clients.append(kwargs)
        return kwargs

    monkeypatch.setattr(providers, "OpenAI", factory)
    return clients


def _fake_azure_openai(monkeypatch):
    clients = []

    def factory(**kwargs):
        clients.append(kwargs)
        return kwargs

    monkeypatch.setattr(providers, "AzureOpenAI", factory)
    return clients


def test_openai_backend(monkeypatch):
    clients = _fake_openai(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    backend = providers.create_backend("openai")

    assert backend.model == "gpt-5.6-sol"
    assert clients == [{"api_key": "openai-key"}]
    assert backend.supports_stateful_responses


def test_azure_backend_uses_v1_endpoint_and_deployment(monkeypatch):
    clients = _fake_openai(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://cv.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "cv-deployment")

    backend = providers.create_backend("azure")

    assert backend.model == "cv-deployment"
    assert clients == [{
        "api_key": "azure-key",
        "base_url": "https://cv.openai.azure.com/openai/v1/",
    }]


def test_azure_openai_backend_uses_dedicated_sdk_client(monkeypatch):
    clients = _fake_azure_openai(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://cv.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "cv-deployment")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")

    backend = providers.create_backend("azure-openai")

    assert backend.provider == "azure-openai"
    assert backend.model == "cv-deployment"
    assert clients == [{
        "api_key": "azure-key",
        "azure_endpoint": "https://cv.openai.azure.com",
        "api_version": "2025-04-01-preview",
    }]


def test_azure_openai_backend_accepts_standard_api_version_variable(monkeypatch):
    clients = _fake_azure_openai(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://cv.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "cv-deployment")
    monkeypatch.delenv("AZURE_OPENAI_API_VERSION", raising=False)
    monkeypatch.setenv("OPENAI_API_VERSION", "2025-04-01-preview")

    providers.create_backend("azure-openai")

    assert clients[0]["api_version"] == "2025-04-01-preview"


def test_ollama_backend_is_stateless(monkeypatch):
    clients = _fake_openai(monkeypatch)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    backend = providers.create_backend("ollama")

    assert backend.model == "qwen3:8b"
    assert not backend.supports_stateful_responses
    assert backend.response_options == {}
    assert clients == [{
        "api_key": "ollama",
        "base_url": "http://localhost:11434/v1/",
    }]


def test_azure_backend_lists_missing_settings(monkeypatch):
    _fake_openai(monkeypatch)
    for name in (
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(providers.ProviderConfigError, match="AZURE_OPENAI_API_KEY"):
        providers.create_backend("azure")


def test_azure_openai_backend_requires_api_version(monkeypatch):
    _fake_azure_openai(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://cv.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "cv-deployment")
    monkeypatch.delenv("AZURE_OPENAI_API_VERSION", raising=False)
    monkeypatch.delenv("OPENAI_API_VERSION", raising=False)

    with pytest.raises(providers.ProviderConfigError, match="OPENAI_API_VERSION"):
        providers.create_backend("azure-openai")
