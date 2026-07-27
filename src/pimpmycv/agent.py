from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
import json
import logging
from pathlib import Path
from string import Template
from typing import Any, Callable, TypedDict

from langgraph.graph import StateGraph, END

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


def rewrite_user_instructions(
    backend: Any,
    user_instructions: str,
) -> str:
    """Rewrite user instructions to make them clearer and more actionable."""
    logger = logging.getLogger(__name__)
    logger.debug("[AGENT] rewrite_user_instructions() called - instructions length=%d chars", len(user_instructions))
    
    if not user_instructions or not user_instructions.strip():
        logger.debug("[AGENT] No user instructions to rewrite")
        return user_instructions
    
    system_prompt = load_prompt("rewrite_instructions.md")
    
    logger.info("Rewriting user instructions with model %s.", backend.model)
    response = backend.call_model(
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": "## User instructions:\n"+ user_instructions.strip()}],
        tools=[],
    )
    
    response_text = backend.extract_text(response)
    
    if not response_text or not response_text.strip():
        logger.warning("Rewriter returned empty response, using original instructions")
        return user_instructions
    
    rewritten = response_text.strip()
    logger.debug("[AGENT] Instructions rewritten - original=%d chars, rewritten=%d chars", len(user_instructions), len(rewritten))
    logger.debug("[AGENT] Instructions rewritten - %s", rewritten)
    logger.info("User instructions rewritten successfully.")
    return rewritten


# LangGraph State Schema

class AgentState(TypedDict):
    """State for the CV tailoring agent."""
    # Input configuration
    cv_tex: str
    job_description: str
    user_instructions: str
    output_tex: Path
    source_dir: Path
    engine: str
    max_attempts: int
    max_feedback_rounds: int
    debug_dir: Path | None
    
    # Conversation state - using simple list without add_messages for custom handling
    messages: list[dict[str, Any]]
    system_prompt: str
    
    # Model outputs
    latex_candidate: str | None
    summary: str | None
    
    # Compilation state
    compile_result: CompileResult | None
    
    # Counters
    failed_attempts: int
    feedback_rounds: int
    draft_number: int
    response_number: int
    candidate_number: int
    
    # Termination flag
    should_terminate: bool
    
    # Backend reference (not serialized)
    backend: Any
    feedback_callback: Callable[[CompileResult, str, int], str | None] | None
    
    # Response tracking for stateful APIs
    last_response: Any
    last_response_id: str | None


# LangGraph Nodes

def generate_candidate(state: AgentState) -> dict:
    """Call the LLM to generate a LaTeX CV candidate."""
    logger = logging.getLogger(__name__)
    logger.debug("[LANGGRAPH] generate_candidate node called")
    
    backend = state["backend"]
    messages = state["messages"]
    
    # Determine if this is the first call or a continuation
    if state["response_number"] == 0:
        logger.info("Requesting initial CV rewrite from model %s.", backend.model)
    else:
        logger.info("Requesting another model response.")
    
    logger.debug("[LANGGRAPH] Calling model API with %d messages", len(messages))
    
    if backend.supports_stateful_responses and state["last_response_id"]:
        response = backend.call_model(
            system_prompt=state["system_prompt"],
            messages=messages,
            tools=TOOLS,
            previous_response_id=state["last_response_id"],
        )
    else:
        response = backend.call_model(
            system_prompt=state["system_prompt"],
            messages=messages,
            tools=TOOLS,
        )
    
    response_id = backend.get_response_id(response)
    logger.debug("[LANGGRAPH] Response received - response_id=%s", response_id)
    
    # Extract reasoning and save if debug enabled
    reasoning = backend.extract_reasoning(response)
    if reasoning and state["debug_dir"]:
        reasoning_path = state["debug_dir"] / f"reasoning-{state['response_number'] + 1:02d}.txt"
        reasoning_path.write_text(reasoning, encoding="utf-8")
        logger.debug("[LANGGRAPH] Saved reasoning to: %s", reasoning_path)
    
    # Save response text if debug enabled
    response_text = backend.extract_text(response)
    if response_text and state["debug_dir"]:
        response_path = state["debug_dir"] / f"response-{state['response_number'] + 1:02d}.txt"
        response_path.write_text(response_text, encoding="utf-8")
        logger.debug("[LANGGRAPH] Saved model response: %s", response_path)
    
    # Extract tool call or text candidate
    calls = backend.extract_tool_calls(response)
    call = calls[0] if calls else None
    
    latex = None
    summary = None
    
    if call is None:
        logger.debug("[LANGGRAPH] No tool call detected, attempting to extract LaTeX from text")
        latex = _text_candidate(response_text)
        summary = "The model returned a LaTeX draft without a change summary."
        if latex is None:
            logger.warning("Response contained neither a function call nor complete LaTeX.")
        else:
            logger.info("Response contained a direct LaTeX candidate.")
    else:
        tool_name, tool_args_str, call_id = backend.get_tool_call_info(call)
        logger.info("Response called %s.", tool_name)
        
        if tool_name != "save_and_compile_cv":
            raise RuntimeError(f"The model called an unknown tool: {tool_name}")
        
        try:
            arguments = json.loads(tool_args_str)
            latex = arguments["latex"]
            summary = arguments["summary"]
            logger.debug("Tool arguments parsed - latex=%d chars, summary=%d chars", len(latex), len(summary))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError("The model returned invalid tool arguments." + str(tool_args_str)) from exc
    
    return {
        "latex_candidate": latex,
        "summary": summary,
        "last_response": response,
        "last_response_id": response_id,
        "response_number": state["response_number"] + 1,
    }


def compile_candidate(state: AgentState) -> dict:
    """Compile the LaTeX candidate and return the result."""
    logger = logging.getLogger(__name__)
    logger.debug("[LANGGRAPH] compile_candidate node called")
    
    latex = state["latex_candidate"]
    
    if latex is None:
        logger.warning("No candidate produced, skipping compilation")
        return {
            "compile_result": None,
            "failed_attempts": state["failed_attempts"] + 1,
        }
    
    if not isinstance(latex, str) or not latex.strip():
        raise RuntimeError("The model returned an empty LaTeX document.")
    
    summary = state["summary"]
    if not isinstance(summary, str) or not summary.strip():
        raise RuntimeError("The model returned an empty rewrite summary.")
    
    candidate_number = state["candidate_number"] + 1
    logger.debug("[LANGGRAPH] Processing candidate %d", candidate_number)
    
    # Preserve preamble
    protected_latex = preserve_preamble(state["cv_tex"], latex)
    if protected_latex != latex:
        logger.debug("Restored the original LaTeX preamble.")
    
    latex = protected_latex
    output_tex = state["output_tex"]
    output_tex.parent.mkdir(parents=True, exist_ok=True)
    output_tex.write_text(latex, encoding="utf-8")
    
    # Save candidate if debug enabled
    if state["debug_dir"]:
        candidate_path = state["debug_dir"] / f"candidate-{candidate_number:02d}.tex"
        candidate_path.write_text(latex, encoding="utf-8")
        logger.debug("Saved candidate: %s", candidate_path)
    
    # Compile
    logger.info("Compiling candidate %d with %s.", candidate_number, state["engine"])
    result = compile_latex(
        output_tex,
        source_dir=state["source_dir"],
        engine=state["engine"],
    )
    
    # Save compiler log if debug enabled
    if state["debug_dir"]:
        compiler_log = state["debug_dir"] / f"candidate-{candidate_number:02d}.log"
        compiler_log.write_text(result.log, encoding="utf-8")
        logger.debug("Saved compiler log: %s", compiler_log)
    
    return {
        "compile_result": result,
        "candidate_number": candidate_number,
    }


def handle_compilation_success(state: AgentState) -> dict:
    """Handle successful compilation and request user feedback if needed."""
    logger = logging.getLogger(__name__)
    logger.debug("[LANGGRAPH] handle_compilation_success node called")
    
    result = state["compile_result"]
    summary = state["summary"]
    draft_number = state["draft_number"] + 1
    
    logger.info("Candidate %d produced a PDF.", state["candidate_number"])
    
    # Check if we should request feedback
    if state["feedback_callback"] is None or state["feedback_rounds"] >= state["max_feedback_rounds"]:
        logger.debug("No feedback callback or max rounds reached, accepting draft")
        return {
            "draft_number": draft_number,
            "failed_attempts": 0,
            "should_terminate": True,
        }
    
    # Request user feedback
    logger.debug("Calling feedback_callback for draft %d", draft_number)
    user_feedback = state["feedback_callback"](
        result,
        summary.strip(),
        draft_number,
    )
    
    if not user_feedback or not user_feedback.strip():
        logger.debug("No user feedback provided, accepting draft")
        return {
            "draft_number": draft_number,
            "failed_attempts": 0,
            "should_terminate": True,
        }
    
    feedback_rounds = state["feedback_rounds"] + 1
    logger.info("Applying user feedback round %d.", feedback_rounds)
    
    # Rewrite feedback for clarity
    rewritten_feedback = rewrite_user_instructions(state["backend"], user_feedback)
    
    # Build continuation messages
    continuation = state["messages"].copy()
    
    # Add tool output if tool was used
    calls = state["backend"].extract_tool_calls(state["last_response"])
    call = calls[0] if calls else None
    if call is not None:
        continuation.extend(state["backend"].format_tool_output(
            call,
            "",
            True,
            {
                "compiler": result.engine,
                "pdf_path": str(result.pdf_path),
            }
        ))
    
    # Add feedback message
    continuation.append({
        "role": "user",
        "content": render_prompt(
            "feedback.md",
            user_feedback=rewritten_feedback.strip(),
        ),
    })
    
    return {
        "messages": continuation,
        "draft_number": draft_number,
        "feedback_rounds": feedback_rounds,
        "failed_attempts": 0,
        "should_terminate": False,
    }


def handle_compilation_failure(state: AgentState) -> dict:
    """Handle compilation failure and return diagnostics to the model."""
    logger = logging.getLogger(__name__)
    logger.debug("[LANGGRAPH] handle_compilation_failure node called")
    
    result = state["compile_result"]
    failed_attempts = state["failed_attempts"] + 1
    
    logger.warning(
        "Candidate %d failed to compile (failed attempt %d/%d).",
        state["candidate_number"],
        failed_attempts,
        state["max_attempts"],
    )
    logger.debug("Compilation log (last 500 chars): %s", result.log[-500:])
    
    # Build continuation messages
    continuation = state["messages"].copy()
    calls = state["backend"].extract_tool_calls(state["last_response"])
    call = calls[0] if calls else None
    
    if call is not None:
        continuation.extend(state["backend"].format_tool_output(
            call,
            "",
            False,
            {
                "attempt": failed_attempts,
                "compiler": result.engine,
                "diagnostics": result.log[-8000:],
            }
        ))
        logger.debug("Sending tool output with failure diagnostics")
    else:
        continuation.append({
            "role": "user",
            "content": render_prompt(
                "compile-failure.md",
                compiler=result.engine,
                diagnostics=result.log[-8000:],
            ),
        })
        logger.debug("Sending compile-failure prompt")
    
    return {
        "messages": continuation,
        "failed_attempts": failed_attempts,
    }


def handle_no_candidate(state: AgentState) -> dict:
    """Handle case where no LaTeX candidate was produced."""
    logger = logging.getLogger(__name__)
    logger.debug("[LANGGRAPH] handle_no_candidate node called")
    
    failed_attempts = state["failed_attempts"] + 1
    logger.warning(
        "No candidate produced (failed attempt %d/%d).",
        failed_attempts,
        state["max_attempts"],
    )
    
    continuation = state["messages"].copy()
    if isinstance(load_prompt("tool-required.md"), str):
        continuation.append({"role": "user", "content": load_prompt("tool-required.md")})
    else:
        continuation.extend(load_prompt("tool-required.md"))
    
    return {
        "messages": continuation,
        "failed_attempts": failed_attempts,
    }


# LangGraph Conditional Edges

def should_compile(state: AgentState) -> str:
    """Determine if we have a valid candidate to compile."""
    if state["latex_candidate"] is None:
        return "no_candidate"
    return "compile"


def check_compilation_result(state: AgentState) -> str:
    """Route based on compilation success."""
    if state["compile_result"] is None:
        return "no_candidate"
    
    if state["compile_result"].success:
        return "success"
    return "failure"


def should_continue(state: AgentState) -> str:
    """Check if we should continue or terminate."""
    # Check termination flag first
    if state["should_terminate"]:
        logger.debug("Termination flag set, ending workflow")
        return "end"
    
    # Check attempt limits
    if state["failed_attempts"] >= state["max_attempts"]:
        logger.warning("Max attempts (%d) reached, aborting", state["max_attempts"])
        return "error"
    
    return "continue"


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
    """Let the model edit, compile, inspect, and retry a LaTeX CV using LangGraph."""
    logger = logging.getLogger(__name__)
    logger.debug("[AGENT] tailor_cv() called with LangGraph - model=%s, engine=%s", backend.model, engine)
    
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if max_feedback_rounds < 0:
        raise ValueError("max_feedback_rounds cannot be negative")
    
    # Rewrite user instructions
    user_instructions = rewrite_user_instructions(backend, user_instructions)
    
    # Create debug directory if needed
    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("Debug directory created: %s", debug_dir)
    
    # Load prompts
    system_prompt = load_prompt("system.md")
    task = render_prompt(
        "task.md",
        cv_tex=cv_tex,
        job_description=job_description,
        user_instructions=user_instructions.strip(),
    )
    
    # Build the LangGraph
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("generate_candidate", generate_candidate)
    workflow.add_node("compile_candidate", compile_candidate)
    workflow.add_node("handle_success", handle_compilation_success)
    workflow.add_node("handle_failure", handle_compilation_failure)
    workflow.add_node("handle_no_candidate", handle_no_candidate)
    
    # Set entry point
    workflow.set_entry_point("generate_candidate")
    
    # Add conditional edges
    workflow.add_conditional_edges(
        "generate_candidate",
        should_compile,
        {
            "compile": "compile_candidate",
            "no_candidate": "handle_no_candidate",
        }
    )
    
    workflow.add_conditional_edges(
        "compile_candidate",
        check_compilation_result,
        {
            "success": "handle_success",
            "failure": "handle_failure",
            "no_candidate": "handle_no_candidate",
        }
    )
    
    workflow.add_conditional_edges(
        "handle_success",
        should_continue,
        {
            "continue": "generate_candidate",
            "end": END,
            "error": END,
        }
    )
    
    workflow.add_conditional_edges(
        "handle_failure",
        should_continue,
        {
            "continue": "generate_candidate",
            "error": END,
        }
    )
    
    workflow.add_conditional_edges(
        "handle_no_candidate",
        should_continue,
        {
            "continue": "generate_candidate",
            "error": END,
        }
    )
    
    # Compile the graph
    app = workflow.compile()
    
    # Initialize state
    initial_state: AgentState = {
        "cv_tex": cv_tex,
        "job_description": job_description,
        "user_instructions": user_instructions,
        "output_tex": output_tex,
        "source_dir": source_dir,
        "engine": engine,
        "max_attempts": max_attempts,
        "max_feedback_rounds": max_feedback_rounds,
        "debug_dir": debug_dir,
        "messages": [{"role": "user", "content": task}],
        "system_prompt": system_prompt,
        "latex_candidate": None,
        "summary": None,
        "compile_result": None,
        "failed_attempts": 0,
        "feedback_rounds": 0,
        "draft_number": 0,
        "response_number": 0,
        "candidate_number": 0,
        "should_terminate": False,
        "backend": backend,
        "feedback_callback": feedback_callback,
        "last_response": None,
        "last_response_id": None,
    }
    
    # Run the graph
    logger.debug("[LANGGRAPH] Starting graph execution")
    final_state = None
    
    # Use invoke to get the final state directly
    try:
        final_state = app.invoke(initial_state)
        logger.debug("[LANGGRAPH] Graph execution completed successfully")
    except Exception as e:
        logger.error("[LANGGRAPH] Graph execution failed: %s", e)
        raise
    
    # Check result
    if final_state and final_state.get("compile_result"):
        result = final_state["compile_result"]
        if result.success:
            logger.info("Successfully produced compilable CV")
            return result
    
    # Error case
    details = ""
    if final_state and final_state.get("compile_result"):
        details = final_state["compile_result"].log[-2000:]
    elif final_state:
        details = f"Failed attempts: {final_state.get('failed_attempts', 0)}"
    else:
        details = "The model never produced a candidate."
    
    raise RuntimeError(
        f"Could not produce a compilable CV after {max_attempts} attempts.\n{details}"
    )
