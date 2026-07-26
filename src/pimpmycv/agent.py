from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
import json
import logging
from pathlib import Path
from string import Template
from typing import Any, Callable

from .compiler import CompileResult, compile_latex


PROMPT_PACKAGE = "pimpmycv.prompts"
DOCUMENT_START = r"\begin{document}"
logger = logging.getLogger(__name__)


@lru_cache
def load_prompt(name: str) -> str:
    """Load a packaged agent prompt by filename."""
    logger.debug("[AGENT] Loading prompt: %s", name)
    content = files(PROMPT_PACKAGE).joinpath(name).read_text(encoding="utf-8").strip()
    logger.debug("[AGENT] Prompt loaded: %s (%d chars)", name, len(content))
    return content


def render_prompt(name: str, **values: str) -> str:
    """Render a packaged prompt without interpreting braces in LaTeX values."""
    logger.debug("[AGENT] Rendering prompt: %s with keys: %s", name, list(values.keys()))
    rendered = Template(load_prompt(name)).substitute(values)
    logger.debug("[AGENT] Prompt rendered: %s (%d chars)", name, len(rendered))
    return rendered


def preserve_preamble(original: str, candidate: str) -> str:
    """Keep the source preamble when the model returns a complete document."""
    logger.debug("[AGENT] preserve_preamble() called - original=%d chars, candidate=%d chars", len(original), len(candidate))
    if DOCUMENT_START not in original or DOCUMENT_START not in candidate:
        logger.debug("[AGENT] No preamble preservation needed (missing document markers)")
        return candidate
    original_preamble = original.split(DOCUMENT_START, 1)[0]
    candidate_body = candidate.split(DOCUMENT_START, 1)[1]
    result = original_preamble + DOCUMENT_START + candidate_body
    logger.debug("[AGENT] Preamble preserved - preamble=%d chars, body=%d chars", len(original_preamble), len(candidate_body))
    return result


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


def _text_candidate(response_text: str) -> str | None:
    """Extract a complete LaTeX document from a non-tool model response."""
    start = response_text.find(DOCUMENT_START)
    document_end = r"\end{document}"
    end = response_text.rfind(document_end)
    if start < 0 or end < start:
        return None
    return response_text[start : end + len(document_end)]


def tailor_cv(
    backend: Any,
    *,
    cv_tex: str,
    job_description: str,
    user_instructions: str = "",
    output_tex: Path,
    source_dir: Path,
    engine: str = "auto",
    max_attempts: int = 4,
    max_feedback_rounds: int = 5,
    feedback_callback: Callable[[CompileResult, str, int], str | None] | None = None,
    debug_dir: Path | None = None,
) -> CompileResult:
    """Let the model edit, compile, inspect, and retry a LaTeX CV."""
    logger.debug("[AGENT] tailor_cv() called - model=%s, engine=%s, max_attempts=%d, max_feedback_rounds=%d", backend.model, engine, max_attempts, max_feedback_rounds)
    logger.debug("[AGENT] CV tex length: %d chars, job description length: %d chars, instructions length: %d chars", len(cv_tex), len(job_description), len(user_instructions))
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if max_feedback_rounds < 0:
        raise ValueError("max_feedback_rounds cannot be negative")

    logger.debug("[AGENT] Loading system prompt and rendering task prompt")
    system_prompt = load_prompt("system.md")
    task = render_prompt(
        "task.md",
        cv_tex=cv_tex,
        job_description=job_description,
        user_instructions=user_instructions.strip(),
    )
    logger.debug("[AGENT] Response options: %s", backend.response_options)
    history: list[dict[str, Any]] = [{"role": "user", "content": task}]
    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("[AGENT] Debug directory created: %s", debug_dir)
    logger.info("Requesting initial CV rewrite from model %s.", backend.model)
    logger.debug("[AGENT] Calling model API with model=%s", backend.model)
    response = backend.call_model(
        system_prompt=system_prompt,
        messages=history,
        tools=TOOLS,
    )
    response_id = backend.get_response_id(response)
    logger.debug("[AGENT] Initial response received - response_id=%s", response_id)

    last_result: CompileResult | None = None
    failed_attempts = 0
    feedback_rounds = 0
    draft_number = 0
    response_number = 1
    candidate_number = 0
    while True:
        logger.debug("[AGENT] --- Processing response %d ---", response_number)
        calls = backend.extract_tool_calls(response)
        call = calls[0] if calls else None
        response_text = backend.extract_text(response)
        # Handle both Responses API (output) and Chat Completions API (choices) formats
        if hasattr(response, "output"):
            item_types = [getattr(item, "type", "unknown") for item in response.output]
        else:
            item_types = ["chat_completion"]
        logger.debug("[AGENT] Response %d item types: %s", response_number, item_types)
        logger.debug("[AGENT] Response %d has %d tool calls", response_number, len(calls))
        
        # Extract and log reasoning if available
        reasoning = backend.extract_reasoning(response)
        if reasoning:
            logger.debug("[AGENT] Response %d contains reasoning (%d chars)", response_number, len(reasoning))
            if debug_dir is not None:
                reasoning_path = debug_dir / f"reasoning-{response_number:02d}.txt"
                reasoning_path.write_text(reasoning, encoding="utf-8")
                logger.debug("[AGENT] Saved reasoning to: %s", reasoning_path)
        
        if debug_dir is not None and response_text:
            response_path = debug_dir / f"response-{response_number:02d}.txt"
            response_path.write_text(response_text, encoding="utf-8")
            logger.debug("[AGENT] Saved model response: %s", response_path)
        if call is None:
            logger.debug("[AGENT] No tool call detected, attempting to extract LaTeX from text")
            latex = _text_candidate(response_text)
            summary = "The model returned a LaTeX draft without a change summary."
            if latex is None:
                logger.warning(
                    "Response %d contained neither a function call nor complete LaTeX.",
                    response_number,
                )
            else:
                logger.info("Response %d contained a direct LaTeX candidate.", response_number)
                logger.debug("[AGENT] Extracted LaTeX length: %d chars", len(latex))
        else:
            tool_name, tool_args_str, call_id = backend.get_tool_call_info(call)
            if call_id:
                logger.debug("[AGENT] Tool call ID: %s", call_id)
            
            logger.info("Response %d called %s.", response_number, tool_name)
            
            if tool_name != "save_and_compile_cv":
                raise RuntimeError(f"The model called an unknown tool: {tool_name}")
            try:
                arguments = json.loads(tool_args_str)
                latex = arguments["latex"]
                summary = arguments["summary"]
                logger.debug("[AGENT] Tool arguments parsed - latex=%d chars, summary=%d chars", len(latex), len(summary))
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise RuntimeError("The model returned invalid tool arguments." + str(tool_args_str)) from exc

        if latex is None:
            failed_attempts += 1
            logger.warning(
                "No candidate produced (failed attempt %d/%d).",
                failed_attempts,
                max_attempts,
            )
            continuation: str | list[dict[str, Any]] = load_prompt("tool-required.md")
            logger.debug("[AGENT] Using tool-required prompt for continuation")
        else:
            if not isinstance(latex, str) or not latex.strip():
                raise RuntimeError("The model returned an empty LaTeX document.")
            if not isinstance(summary, str) or not summary.strip():
                raise RuntimeError("The model returned an empty rewrite summary.")

            candidate_number += 1
            logger.debug("[AGENT] Processing candidate %d", candidate_number)
            protected_latex = preserve_preamble(cv_tex, latex)
            if protected_latex != latex:
                logger.debug("Restored the original LaTeX preamble.")
            latex = protected_latex
            output_tex.parent.mkdir(parents=True, exist_ok=True)
            output_tex.write_text(latex, encoding="utf-8")
            logger.debug("[AGENT] Candidate written to: %s", output_tex)
            if debug_dir is not None:
                candidate_path = debug_dir / f"candidate-{candidate_number:02d}.tex"
                candidate_path.write_text(latex, encoding="utf-8")
                logger.debug("Saved candidate: %s", candidate_path)
            logger.info("Compiling candidate %d with %s.", candidate_number, engine)
            logger.debug("[AGENT] Calling compile_latex() - tex_path=%s, source_dir=%s", output_tex, source_dir)
            last_result = compile_latex(
                output_tex,
                source_dir=source_dir,
                engine=engine,
            )
            logger.debug("[AGENT] Compilation result - success=%s, engine=%s, pdf_path=%s", last_result.success, last_result.engine, last_result.pdf_path)
            if debug_dir is not None:
                compiler_log = debug_dir / f"candidate-{candidate_number:02d}.log"
                compiler_log.write_text(last_result.log, encoding="utf-8")
                logger.debug("Saved compiler log: %s", compiler_log)
            if last_result.success:
                logger.info("Candidate %d produced a PDF.", candidate_number)
                failed_attempts = 0
                draft_number += 1
                logger.debug("[AGENT] Draft number incremented to %d", draft_number)
                if feedback_callback is None or feedback_rounds >= max_feedback_rounds:
                    logger.debug("[AGENT] No feedback callback or max rounds reached, returning result")
                    return last_result
                logger.debug("[AGENT] Calling feedback_callback for draft %d", draft_number)
                user_feedback = feedback_callback(last_result, summary.strip(), draft_number)
                if not user_feedback or not user_feedback.strip():
                    logger.debug("[AGENT] No user feedback provided, accepting draft")
                    return last_result
                feedback_rounds += 1
                logger.info("Applying user feedback round %d.", feedback_rounds)
                logger.debug("[AGENT] User feedback length: %d chars", len(user_feedback))
                continuation = []
                if call is not None:
                    continuation.extend(backend.format_tool_output(
                        call,
                        "",
                        True,
                        {
                            "compiler": last_result.engine,
                            "pdf_path": str(last_result.pdf_path),
                        }
                    ))
                continuation.append(
                    {
                        "role": "user",
                        "content": render_prompt(
                            "feedback.md",
                            user_feedback=user_feedback.strip(),
                        ),
                    }
                )
            else:
                failed_attempts += 1
                logger.warning(
                    "Candidate %d failed to compile (failed attempt %d/%d).",
                    candidate_number,
                    failed_attempts,
                    max_attempts,
                )
                logger.debug("[AGENT] Compilation log (last 500 chars): %s", last_result.log[-500:])
                if call is not None:
                    continuation = backend.format_tool_output(
                        call,
                        "",
                        False,
                        {
                            "attempt": failed_attempts,
                            "compiler": last_result.engine,
                            "diagnostics": last_result.log[-8000:],
                        }
                    )
                    logger.debug("[AGENT] Sending tool output with failure diagnostics")
                else:
                    continuation = [{
                        "role": "user",
                        "content": render_prompt(
                            "compile-failure.md",
                            compiler=last_result.engine,
                            diagnostics=last_result.log[-8000:],
                        ),
                    }]
                    logger.debug("[AGENT] Sending compile-failure prompt")

        if failed_attempts >= max_attempts:
            logger.warning("[AGENT] Max attempts (%d) reached, aborting", max_attempts)
            break

        logger.info("Requesting another model response.")
        logger.debug("[AGENT] Calling model API for response %d", response_number + 1)
        
        if backend.supports_stateful_responses:
            response = backend.call_model(
                system_prompt=system_prompt,
                messages=continuation,
                tools=TOOLS,
                previous_response_id=backend.get_response_id(response),
            )
        else:
            logger.debug("[AGENT] Using stateless API, serialising history")
            history.extend(backend.serialize_for_continuation(response))
            if isinstance(continuation, str):
                history.append({"role": "user", "content": continuation})
            else:
                history.extend(continuation)
            response = backend.call_model(
                system_prompt=system_prompt,
                messages=history,
                tools=TOOLS,
            )
        response_id = backend.get_response_id(response)
        logger.debug("[AGENT] Response %d received - response_id=%s", response_number + 1, response_id)
        response_number += 1

    details = last_result.log[-2000:] if last_result else "The model never produced a candidate."
    raise RuntimeError(
        f"Could not produce a compilable CV after {max_attempts} attempts.\n{details}"
    )
