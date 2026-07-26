from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
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
    import logging
    logger = logging.getLogger(__name__)
    logger.debug("[PROVIDERS] dotenv loaded successfully")
else:
    import logging
    logger = logging.getLogger(__name__)
    logger.debug("[PROVIDERS] dotenv not available, using environment variables only")


@dataclass(frozen=True)
class Backend:
    provider: ProviderName
    client: Any
    model: str
    supports_stateful_responses: bool = True
    response_options: dict[str, Any] = field(default_factory=dict)
    
    def _convert_tools_for_chat_api(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert Responses API tools format to Chat Completions API format."""
        chat_tools = []
        for tool in tools:
            if tool.get("type") == "function":
                chat_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["parameters"],
                    }
                })
        return chat_tools
    
    def call_model(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> Any:
        """Call the appropriate API (Responses or Chat Completions) based on provider capabilities."""
        logger = logging.getLogger(__name__)
        
        if self.supports_stateful_responses and previous_response_id is not None:
            # Use stateful Responses API
            logger.debug("[PROVIDERS] Using stateful Responses API with previous_response_id=%s", previous_response_id)
            return self.client.responses.create(
                model=self.model,
                instructions=system_prompt,
                input=messages,
                tools=tools,
                previous_response_id=previous_response_id,
                **self.response_options,
            )
        elif self.supports_stateful_responses:
            # Use stateless Responses API
            logger.debug("[PROVIDERS] Using stateless Responses API")
            return self.client.responses.create(
                model=self.model,
                instructions=system_prompt,
                input=messages,
                tools=tools,
                **self.response_options,
            )
        else:
            # Fall back to Chat Completions API
            logger.debug("[PROVIDERS] Using Chat Completions API")
            # Convert messages format: prepend system prompt
            chat_messages = [{"role": "system", "content": system_prompt}] + messages
            # Filter out function_call_output items which are Responses API specific
            chat_messages = [m for m in chat_messages if m.get("type") != "function_call_output"]
            
            # Convert tools format
            chat_tools = self._convert_tools_for_chat_api(tools)
            
            # Map response_options to chat completions parameters
            chat_options = {}
            if "tool_choice" in self.response_options:
                chat_options["tool_choice"] = self.response_options["tool_choice"]
            if "parallel_tool_calls" in self.response_options:
                chat_options["parallel_tool_calls"] = self.response_options["parallel_tool_calls"]
            
            return self.client.chat.completions.create(
                model=self.model,
                messages=chat_messages,
                tools=chat_tools,
                **chat_options,
            )
    
    def extract_tool_calls(self, response: Any) -> list[Any]:
        """Extract tool calls from either Responses API or Chat Completions API response."""
        # Try Responses API format first
        if hasattr(response, "output"):
            return [
                item
                for item in response.output
                if getattr(item, "type", None) == "function_call"
            ]
        # Try Chat Completions API format
        if hasattr(response, "choices") and response.choices:
            message = response.choices[0].message
            if hasattr(message, "tool_calls") and message.tool_calls:
                return message.tool_calls
        return []
    
    def extract_text(self, response: Any) -> str:
        """Collect ordinary text from either Responses API or Chat Completions API result."""
        # Try Chat Completions API format first
        if hasattr(response, "choices") and response.choices:
            message = response.choices[0].message
            if hasattr(message, "content") and message.content:
                return message.content
        # Try Responses API format
        text = getattr(response, "output_text", "") or ""
        if not text and hasattr(response, "output"):
            parts = []
            for item in response.output:
                if getattr(item, "type", None) != "message":
                    continue
                for content in getattr(item, "content", []):
                    value = getattr(content, "text", None)
                    if isinstance(value, str):
                        parts.append(value)
            text = "\n".join(parts)
        return text
    
    def extract_reasoning(self, response: Any) -> str | None:
        """Extract reasoning/thinking content from Responses API result (not available in Chat Completions)."""
        # Chat Completions API doesn't support reasoning in the same way
        if hasattr(response, "choices"):
            return None
        # Check for reasoning in the response object (OpenAI Responses API)
        reasoning = getattr(response, "reasoning", None)
        if reasoning:
            return str(reasoning)
        # Check for reasoning in output items
        if hasattr(response, "output"):
            for item in response.output:
                item_reasoning = getattr(item, "reasoning", None)
                if item_reasoning:
                    return str(item_reasoning)
                # Check for reasoning in content blocks
                if getattr(item, "type", None) == "message":
                    for content in getattr(item, "content", []):
                        content_reasoning = getattr(content, "reasoning", None)
                        if content_reasoning:
                            return str(content_reasoning)
        return None
    
    def get_response_id(self, response: Any) -> str | None:
        """Get the response ID for stateful operations."""
        return getattr(response, "id", None)
    
    def get_tool_call_info(self, tool_call: Any) -> tuple[str, str, str | None]:
        """Extract tool call name, arguments, and ID from either API format.
        
        Returns:
            tuple of (tool_name, tool_arguments_string, call_id)
        """
        # Handle both Responses API and Chat Completions API tool call formats
        call_id = getattr(tool_call, "call_id", None)
        
        if hasattr(tool_call, "name"):
            tool_name = tool_call.name
            tool_args_str = tool_call.arguments
        else:
            # Chat Completions API format
            tool_name = tool_call.function.name
            tool_args_str = tool_call.function.arguments
            call_id = getattr(tool_call, "id", None)
        
        return tool_name, tool_args_str, call_id
    
    def format_tool_output(
        self,
        tool_call: Any,
        output: str,
        is_success: bool,
        extra_fields: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Format tool output for continuation messages based on API format.
        
        Args:
            tool_call: The tool call object from the response
            output: The output string to send back
            is_success: Whether the tool call succeeded
            extra_fields: Additional fields to include in the output JSON
        
        Returns:
            List of message dictionaries for the continuation
        """
        tool_name, _, call_id = self.get_tool_call_info(tool_call)
        output_data = {"success": is_success}
        if extra_fields:
            output_data.update(extra_fields)
        output_json = json.dumps(output_data)
        
        if self.supports_stateful_responses and call_id:
            # Responses API format
            return [{
                "type": "function_call_output",
                "call_id": call_id,
                "output": output_json,
            }]
        else:
            # Chat Completions API format
            tool_id = getattr(tool_call, "id", "call_1")
            return [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tool_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": getattr(tool_call, "arguments", getattr(tool_call.function, "arguments", "{}")),
                        }
                    }]
                },
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": output_json,
                }
            ]
    
    def serialize_for_continuation(self, response: Any) -> list[dict[str, Any]]:
        """Turn response output items back into valid stateless input items."""
        # Chat Completions API doesn't need serialization for stateless mode
        # since we use the messages list directly
        if hasattr(response, "choices"):
            return []
        serialised = []
        for item in response.output:
            if hasattr(item, "model_dump"):
                serialised.append(item.model_dump(exclude_none=True))
            elif isinstance(item, dict):
                serialised.append(item)
            else:
                serialised.append({key: value for key, value in vars(item).items()})
        return serialised


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
        logger.debug("[PROVIDERS] Azure config - endpoint=%s, deployment=%s, api_version=%s", azure_endpoint, deployment, api_version)
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
            supports_stateful_responses=False,
            response_options={},
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
