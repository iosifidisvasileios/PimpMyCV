from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
import json
from pathlib import Path
from string import Template
from typing import Any, Callable

from .compiler import CompileResult, compile_latex


PROMPT_PACKAGE = "pimpmycv.prompts"


@lru_cache
def load_prompt(name: str) -> str:
    """Load a packaged agent prompt by filename."""
    return files(PROMPT_PACKAGE).joinpath(name).read_text(encoding="utf-8").strip()


def render_prompt(name: str, **values: str) -> str:
    """Render a packaged prompt without interpreting braces in LaTeX values."""
    return Template(load_prompt(name)).substitute(values)


TOOLS = [
    {
        "type": "function",
        "name": "save_and_compile_cv",
        "description": (
            "Replace the tailored .tex candidate and compile it. Returns success, "
            "the PDF path, and compiler diagnostics. Call again with corrected "
            "LaTeX when success is false."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "latex": {
                    "type": "string",
                    "description": "The complete compilable LaTeX document.",
                },
                "summary": {
                    "type": "string",
                    "description": (
                        "A concise, user-facing summary of the important wording, "
                        "ordering, and emphasis changes in this candidate."
                    ),
                }
            },
            "required": ["latex", "summary"],
            "additionalProperties": False,
        },
        "strict": True,
    }
]


def _tool_calls(response: Any) -> list[Any]:
    return [item for item in response.output if item.type == "function_call"]


def _serialise_output(response: Any) -> list[dict[str, Any]]:
    """Turn response output items back into valid stateless input items."""
    serialised = []
    for item in response.output:
        if hasattr(item, "model_dump"):
            serialised.append(item.model_dump(exclude_none=True))
        elif isinstance(item, dict):
            serialised.append(item)
        else:
            serialised.append({key: value for key, value in vars(item).items()})
    return serialised


def tailor_cv(
    client: Any,
    *,
    cv_tex: str,
    job_description: str,
    output_tex: Path,
    source_dir: Path,
    model: str = "gpt-5.6-sol",
    engine: str = "auto",
    max_attempts: int = 4,
    max_feedback_rounds: int = 5,
    feedback_callback: Callable[[CompileResult, str, int], str | None] | None = None,
    supports_stateful_responses: bool = True,
    response_options: dict[str, Any] | None = None,
) -> CompileResult:
    """Let the model edit, compile, inspect, and retry a LaTeX CV."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if max_feedback_rounds < 0:
        raise ValueError("max_feedback_rounds cannot be negative")

    system_prompt = load_prompt("system.md")
    task = render_prompt(
        "task.md",
        cv_tex=cv_tex,
        job_description=job_description,
    )
    if response_options is None:
        response_options = {
            "reasoning": {"effort": "medium"},
            "tool_choice": "required",
            "parallel_tool_calls": False,
        }
    history: list[dict[str, Any]] = [{"role": "user", "content": task}]
    response = client.responses.create(
        model=model,
        instructions=system_prompt,
        input=history,
        tools=TOOLS,
        **response_options,
    )

    last_result: CompileResult | None = None
    failed_attempts = 0
    feedback_rounds = 0
    draft_number = 0
    while True:
        calls = _tool_calls(response)
        if not calls:
            failed_attempts += 1
            continuation: str | list[dict[str, Any]] = load_prompt("tool-required.md")
        else:
            call = calls[0]
            if call.name != "save_and_compile_cv":
                raise RuntimeError(f"The model called an unknown tool: {call.name}")
            try:
                arguments = json.loads(call.arguments)
                latex = arguments["latex"]
                summary = arguments["summary"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise RuntimeError("The model returned invalid tool arguments.") from exc
            if not isinstance(latex, str) or not latex.strip():
                raise RuntimeError("The model returned an empty LaTeX document.")
            if not isinstance(summary, str) or not summary.strip():
                raise RuntimeError("The model returned an empty rewrite summary.")

            output_tex.parent.mkdir(parents=True, exist_ok=True)
            output_tex.write_text(latex, encoding="utf-8")
            last_result = compile_latex(
                output_tex,
                source_dir=source_dir,
                engine=engine,
            )
            if last_result.success:
                failed_attempts = 0
                draft_number += 1
                if feedback_callback is None or feedback_rounds >= max_feedback_rounds:
                    return last_result
                user_feedback = feedback_callback(last_result, summary.strip(), draft_number)
                if not user_feedback or not user_feedback.strip():
                    return last_result
                feedback_rounds += 1
                continuation = [
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps({
                            "success": True,
                            "compiler": last_result.engine,
                            "pdf_path": str(last_result.pdf_path),
                        }),
                    },
                    {
                        "role": "user",
                        "content": render_prompt(
                            "feedback.md",
                            user_feedback=user_feedback.strip(),
                        ),
                    },
                ]
            else:
                failed_attempts += 1
                continuation = [{
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps({
                        "success": False,
                        "attempt": failed_attempts,
                        "compiler": last_result.engine,
                        "diagnostics": last_result.log[-8000:],
                    }),
                }]

        if failed_attempts >= max_attempts:
            break

        next_request: dict[str, Any] = {
            "model": model,
            "instructions": system_prompt,
            "tools": TOOLS,
            **response_options,
        }
        if supports_stateful_responses:
            next_request.update(
                previous_response_id=response.id,
                input=continuation,
            )
        else:
            history.extend(_serialise_output(response))
            if isinstance(continuation, str):
                history.append({"role": "user", "content": continuation})
            else:
                history.extend(continuation)
            next_request["input"] = history
        response = client.responses.create(**next_request)

    details = last_result.log[-2000:] if last_result else "The model never produced a candidate."
    raise RuntimeError(
        f"Could not produce a compilable CV after {max_attempts} attempts.\n{details}"
    )
