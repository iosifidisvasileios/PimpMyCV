from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
from queue import Empty, Queue
import shutil
import sys
import tempfile
from threading import Event, Thread
from typing import Any, Callable
import urllib.request
import urllib.error

from pimpmycv.agent import tailor_cv
from pimpmycv.archive import extract_cv_archive, write_tailored_archive
from pimpmycv.compiler import CompileResult, SUPPORTED_ENGINES, find_engine
from pimpmycv.providers import (
    PROVIDERS,
    ProviderName,
    create_backend,
)


def get_ollama_models() -> tuple[str, ...]:
    """Fetch available models from Ollama API."""
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            models = sorted([model["name"] for model in data.get("models", [])])
            return tuple(models) if models else ("qwen3:8b",)
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, TimeoutError):
        # Fallback to default models if Ollama is not available
        return ("qwen3:8b",)


@dataclass(frozen=True)
class TailoredFiles:
    """Downloadable output produced by a GUI run."""

    pdf: bytes
    project_zip: bytes
    engine: str
    provider: str
    model: str
    main_tex: str
    pdf_filename: str
    pdf_directory: str | None


@dataclass(frozen=True)
class DraftReview:
    """A compiled draft waiting for review in the browser."""

    pdf: bytes
    summary: str
    number: int


@dataclass(frozen=True)
class JobEvent:
    """A thread-safe update emitted by a GUI tailoring job."""

    kind: str
    payload: Any


class GuiTailoringJob:
    """Run the synchronous agent while Streamlit remains interactive."""

    def __init__(self, **runner_options: Any) -> None:
        self.events: Queue[JobEvent] = Queue()
        self._feedback: Queue[str | None] = Queue()
        self._waiting_for_feedback = Event()
        self._runner_options = runner_options
        self._thread = Thread(target=self._run, daemon=True)

    def start(self) -> GuiTailoringJob:
        self._thread.start()
        return self

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    @property
    def awaiting_feedback(self) -> bool:
        return self._waiting_for_feedback.is_set()

    def submit_feedback(self, feedback: str | None) -> None:
        if not self.awaiting_feedback:
            raise RuntimeError("This tailoring job is not waiting for feedback.")
        self._waiting_for_feedback.clear()
        self._feedback.put(feedback.strip() if feedback else None)

    def drain_events(self) -> list[JobEvent]:
        updates: list[JobEvent] = []
        while True:
            try:
                updates.append(self.events.get_nowait())
            except Empty:
                return updates

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def _request_feedback(
        self,
        result: CompileResult,
        summary: str,
        draft_number: int,
    ) -> str | None:
        self._waiting_for_feedback.set()
        self.events.put(
            JobEvent(
                "draft",
                DraftReview(
                    pdf=result.pdf_path.read_bytes(),
                    summary=summary,
                    number=draft_number,
                ),
            )
        )
        feedback = self._feedback.get()
        self._waiting_for_feedback.clear()
        return feedback

    def _run(self) -> None:
        try:
            output = tailor_uploaded_cv(
                **self._runner_options,
                feedback_callback=self._request_feedback,
            )
            self.events.put(JobEvent("complete", output))
        except Exception as exc:
            self.events.put(JobEvent("error", str(exc) or type(exc).__name__))


def default_model(provider: str) -> str:
    """Return the same provider-specific default used by the CLI."""
    if provider == "ollama":
        return os.getenv("OLLAMA_MODEL", "qwen3:8b")
    if provider == "azure":
        return os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
    return os.getenv("PIMPMYCV_MODEL", "gpt-5.6-sol")


def tailor_uploaded_cv(
    *,
    cv_zip: bytes,
    job_description: str,
    instructions: str = "",
    main_tex: str | None = None,
    provider: ProviderName = "openai",
    model: str | None = None,
    endpoint: str | None = None,
    api_key: str | None = None,
    api_version: str | None = None,
    engine: str = "auto",
    max_attempts: int = 4,
    max_feedback_rounds: int = 5,
    feedback_callback: Callable[
        [CompileResult, str, int],
        str | None,
    ]
    | None = None,
    pdf_filename: str = "tailored_cv.pdf",
    pdf_directory: str | None = None,
) -> TailoredFiles:
    """Run the existing tailoring pipeline against browser-uploaded inputs."""
    if not cv_zip:
        raise ValueError("Upload a non-empty CV project ZIP.")
    if not job_description.strip():
        raise ValueError("Enter or upload a job description.")
    if max_attempts < 1:
        raise ValueError("Maximum attempts must be at least 1.")
    if max_feedback_rounds < 0:
        raise ValueError("Maximum feedback rounds cannot be negative.")

    selected_engine = find_engine(engine)
    backend = create_backend(
        provider,
        model=model or None,
        endpoint=endpoint or None,
        api_key=api_key or None,
        api_version=api_version or None,
    )

    # Determine output directory
    if pdf_directory:
        output_dir = Path(pdf_directory).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        use_temp = False
    else:
        use_temp = True

    with tempfile.TemporaryDirectory(prefix="pimpmycv-gui-") as temp_dir:
        work_dir = Path(temp_dir)
        archive_path = work_dir / "uploaded_cv.zip"
        output_zip = work_dir / "tailored_cv.zip"
        archive_path.write_bytes(cv_zip)

        with extract_cv_archive(archive_path, main_tex or None) as project:
            result = tailor_cv(
                backend,
                cv_tex=project.main_tex.read_text(encoding="utf-8"),
                job_description=job_description,
                user_instructions=instructions,
                output_tex=project.main_tex,
                source_dir=project.main_tex.parent,
                engine=selected_engine,
                max_attempts=max_attempts,
                max_feedback_rounds=max_feedback_rounds,
                feedback_callback=feedback_callback,
            )
            
            # Save PDF to custom directory if specified
            if not use_temp:
                pdf_save_path = output_dir / pdf_filename
                shutil.copy2(result.pdf_path, pdf_save_path)
                output_zip_path = output_dir / "tailored_cv.zip"
                write_tailored_archive(project, output_zip_path, pdf_path=result.pdf_path)
                pdf_bytes = pdf_save_path.read_bytes()
                zip_bytes = output_zip_path.read_bytes()
            else:
                write_tailored_archive(project, output_zip, pdf_path=result.pdf_path)
                pdf_bytes = result.pdf_path.read_bytes()
                zip_bytes = output_zip.read_bytes()
            
            return TailoredFiles(
                pdf=pdf_bytes,
                project_zip=zip_bytes,
                engine=result.engine,
                provider=backend.provider,
                model=backend.model,
                main_tex=project.main_relative_path.as_posix(),
                pdf_filename=pdf_filename,
                pdf_directory=str(output_dir) if not use_temp else None,
            )


def _decode_upload(upload, label: str) -> str:
    if upload is None:
        return ""
    try:
        return upload.getvalue().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be UTF-8 text.") from exc


def _pdf_data_url(pdf: bytes) -> str:
    """Build a browser-safe data URL for an inline PDF."""
    encoded_pdf = base64.b64encode(pdf).decode("ascii")
    return f"data:application/pdf;base64,{encoded_pdf}"


def _render_pdf(st, pdf: bytes, *, title: str, height: int = 760) -> None:
    st.caption(title)
    st.iframe(
        _pdf_data_url(pdf),
        height=height,
    )


def render_app() -> None:
    """Render the Streamlit application."""
    import streamlit as st

    st.set_page_config(
        page_title="PimpMyCV",
        page_icon="📄",
        layout="centered",
    )
    st.markdown(
        """
        <style>
        .block-container {max-width: 860px; padding-top: 2.5rem;}
        [data-testid="stFileUploader"] {border-radius: 14px;}
        .pmcv-kicker {
            color: #0f766e; font-size: .8rem; font-weight: 750;
            letter-spacing: .12em; text-transform: uppercase;
        }
        .pmcv-subtitle {color: #5d6778; font-size: 1.05rem; margin-bottom: 1.4rem;}
        </style>
        <div class="pmcv-kicker">LaTeX CV tailoring</div>
        """,
        unsafe_allow_html=True,
    )
    st.title("PimpMyCV")
    st.markdown(
        '<div class="pmcv-subtitle">Upload your CV project, add a role, '
        "review each draft, and guide revisions before downloading.</div>",
        unsafe_allow_html=True,
    )

    current_job = st.session_state.get("tailoring_job")
    job_is_running = bool(current_job and current_job.running)

    with st.sidebar:
        st.header("Model & build")
        provider = st.selectbox("Provider", PROVIDERS)
        if provider == "ollama":
            ollama_models = get_ollama_models()
            configured_model = os.getenv("OLLAMA_MODEL", ollama_models[0])
            model_index = (
                ollama_models.index(configured_model)
                if configured_model in ollama_models
                else 0
            )
            model = st.selectbox(
                "Model",
                ollama_models,
                index=model_index,
            )
        else:
            model = st.text_input(
                "Model / deployment",
                value=default_model(provider),
            )
        endpoint = st.text_input(
            "Endpoint override",
            placeholder="Use environment configuration",
        )
        api_key = st.text_input(
            "API key",
            type="password",
            placeholder="Use environment configuration",
            help="Kept in this browser session and passed directly to the provider client.",
        )
        api_version = ""
        if provider == "azure":
            api_version = st.text_input(
                "Azure API version",
                value=os.getenv("AZURE_OPENAI_API_VERSION", ""),
            )
        engine = st.selectbox("LaTeX engine", ("auto", *SUPPORTED_ENGINES))
        max_attempts = st.number_input(
            "Compile/fix attempts",
            min_value=1,
            max_value=10,
            value=4,
        )
        max_feedback_rounds = st.number_input(
            "Maximum revisions",
            min_value=1,
            max_value=10,
            value=5,
            help="How many feedback-driven rewrites you can request.",
        )
        with st.expander("Advanced"):
            main_tex = st.text_input(
                "Main .tex path",
                placeholder="Auto-detect",
                help="Only needed when the ZIP contains multiple LaTeX documents.",
            )
            pdf_filename = st.text_input(
                "PDF filename",
                value="tailored_cv.pdf",
                help="Custom filename for the downloaded PDF.",
            )
            default_download_dir = str(Path.home() / "Downloads")
            pdf_directory = st.text_input(
                "PDF directory",
                value=default_download_dir,
                help="Note: Actual save location is controlled by your browser's download settings.",
            )

    with st.form("tailor_cv"):
        cv_upload = st.file_uploader(
            "CV project",
            type=["zip"],
            help="Include the main .tex document and all referenced assets.",
        )
        job_upload = st.file_uploader(
            "Job description file (optional)",
            type=["txt"],
        )
        job_text = st.text_area(
            "Job description",
            height=220,
            placeholder="Paste the job description here, or upload a .txt file above.",
        )
        instructions_upload = st.file_uploader(
            "Additional instructions file (optional)",
            type=["txt", "md"],
        )
        instructions_text = st.text_area(
            "Additional instructions (optional)",
            height=110,
            placeholder="For example: Keep it to one page and emphasize platform work.",
        )
        submitted = st.form_submit_button(
            "Tailor my CV",
            type="primary",
            use_container_width=True,
            disabled=job_is_running,
        )

    if submitted:
        try:
            if cv_upload is None:
                raise ValueError("Upload your CV project ZIP.")
            resolved_job = job_text.strip() or _decode_upload(
                job_upload, "Job description"
            ).strip()
            resolved_instructions = instructions_text.strip() or _decode_upload(
                instructions_upload, "Instructions"
            ).strip()
            st.session_state.pop("tailored_files", None)
            st.session_state.pop("draft_review", None)
            st.session_state.pop("tailoring_error", None)
            st.session_state["tailoring_job"] = GuiTailoringJob(
                cv_zip=cv_upload.getvalue(),
                job_description=resolved_job,
                instructions=resolved_instructions,
                main_tex=main_tex.strip() or None,
                provider=provider,
                model=model.strip() or None,
                endpoint=endpoint.strip() or None,
                api_key=api_key.strip() or None,
                api_version=api_version.strip() or None,
                engine=engine,
                max_attempts=int(max_attempts),
                max_feedback_rounds=int(max_feedback_rounds),
                pdf_filename=pdf_filename.strip() or "tailored_cv.pdf",
                pdf_directory=pdf_directory.strip() or None,
            ).start()
            st.rerun()
        except ValueError as exc:
            st.session_state.pop("tailored_files", None)
            st.error(str(exc))

    current_job = st.session_state.get("tailoring_job")
    should_poll = (
        current_job is not None
        and current_job.running
        and not current_job.awaiting_feedback
    )

    @st.fragment(run_every=0.75 if should_poll else None)
    def review_panel() -> None:
        job = st.session_state.get("tailoring_job")
        received_update = False
        if job is not None:
            for update in job.drain_events():
                received_update = True
                if update.kind == "draft":
                    st.session_state["draft_review"] = update.payload
                elif update.kind == "complete":
                    st.session_state["tailored_files"] = update.payload
                    st.session_state.pop("draft_review", None)
                elif update.kind == "error":
                    st.session_state["tailoring_error"] = update.payload
                    st.session_state.pop("draft_review", None)
        if received_update:
            st.rerun()

        error = st.session_state.get("tailoring_error")
        if error:
            st.error(error)

        draft = st.session_state.get("draft_review")
        if draft is not None:
            st.divider()
            st.subheader(f"Review draft {draft.number}")
            st.caption("Inspect the PDF, then accept it or request another revision.")
            _render_pdf(
                st,
                draft.pdf,
                title=f"CV draft {draft.number}",
                height=760,
            )
            with st.expander("What changed", expanded=True):
                st.write(draft.summary)

            with st.form(f"draft-feedback-{draft.number}"):
                feedback = st.text_area(
                    "Revision feedback",
                    placeholder=(
                        "For example: Shorten the summary and emphasize the "
                        "distributed systems work."
                    ),
                    height=110,
                )
                revise_column, accept_column = st.columns(2)
                with revise_column:
                    revise = st.form_submit_button(
                        "Request revision",
                        type="primary",
                        use_container_width=True,
                    )
                with accept_column:
                    accept = st.form_submit_button(
                        "Accept this draft",
                        use_container_width=True,
                    )

            if revise:
                if not feedback.strip():
                    st.warning("Enter feedback before requesting a revision.")
                elif job is not None:
                    job.submit_feedback(feedback)
                    st.session_state.pop("draft_review", None)
                    st.rerun()
            if accept and job is not None:
                job.submit_feedback(None)
                st.session_state.pop("draft_review", None)
                st.rerun()
        elif job is not None and job.running:
            st.info(
                "The agent is tailoring and compiling your next draft. "
                "This can take a few minutes."
            )

        output = st.session_state.get("tailored_files")
        if output is not None:
            st.divider()
            st.success("Your tailored CV is ready.")
            st.caption(
                f"{output.provider} · {output.model} · "
                f"{output.engine} · {output.main_tex}"
            )
            if output.pdf_directory:
                st.caption(f"💾 Suggested save location: {output.pdf_directory}")
            _render_pdf(
                st,
                output.pdf,
                title="Final tailored CV",
                height=760,
            )
            pdf_column, zip_column = st.columns(2)
            with pdf_column:
                st.download_button(
                    "Download PDF",
                    output.pdf,
                    file_name=output.pdf_filename,
                    mime="application/pdf",
                    type="primary",
                    use_container_width=True,
                )
            with zip_column:
                st.download_button(
                    "Download LaTeX project",
                    output.project_zip,
                    file_name="tailored_cv.zip",
                    mime="application/zip",
                    use_container_width=True,
                )

    review_panel()

    st.caption(
        "Your CV and job description are sent only to the provider endpoint you select."
    )


def launch() -> None:
    """Launch the packaged app through the Streamlit CLI."""
    try:
        from streamlit.web import cli as streamlit_cli
    except ImportError as exc:
        raise SystemExit(
            'The GUI is optional. Install it with: pip install -e ".[gui]"'
        ) from exc

    sys.argv = ["streamlit", "run", str(Path(__file__).resolve())]
    raise SystemExit(streamlit_cli.main())


if __name__ == "__main__":
    render_app()
