from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys
import tempfile

from openai import OpenAIError

from pimpmycv.agent import tailor_cv
from pimpmycv.archive import ArchiveError, extract_cv_archive, write_tailored_archive
from pimpmycv.compiler import SUPPORTED_ENGINES, find_engine
from pimpmycv.providers import (
    PROVIDERS,
    ProviderConfigError,
    ProviderName,
    create_backend,
)

OLLAMA_MODELS = ("gemma4:26b", "gemma4:e4b")


@dataclass(frozen=True)
class TailoredFiles:
    """Downloadable output produced by a GUI run."""

    pdf: bytes
    project_zip: bytes
    engine: str
    provider: str
    model: str
    main_tex: str


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
) -> TailoredFiles:
    """Run the existing tailoring pipeline against browser-uploaded inputs."""
    if not cv_zip:
        raise ValueError("Upload a non-empty CV project ZIP.")
    if not job_description.strip():
        raise ValueError("Enter or upload a job description.")
    if max_attempts < 1:
        raise ValueError("Maximum attempts must be at least 1.")

    selected_engine = find_engine(engine)
    backend = create_backend(
        provider,
        model=model or None,
        endpoint=endpoint or None,
        api_key=api_key or None,
        api_version=api_version or None,
    )

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
                max_feedback_rounds=0,
                feedback_callback=None,
            )
            write_tailored_archive(project, output_zip, pdf_path=result.pdf_path)
            return TailoredFiles(
                pdf=result.pdf_path.read_bytes(),
                project_zip=output_zip.read_bytes(),
                engine=result.engine,
                provider=backend.provider,
                model=backend.model,
                main_tex=project.main_relative_path.as_posix(),
            )


def _decode_upload(upload, label: str) -> str:
    if upload is None:
        return ""
    try:
        return upload.getvalue().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be UTF-8 text.") from exc


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
        "and download a tailored PDF plus the complete LaTeX source.</div>",
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Model & build")
        provider = st.selectbox("Provider", PROVIDERS)
        if provider == "ollama":
            configured_model = os.getenv("OLLAMA_MODEL", OLLAMA_MODELS[0])
            model_index = (
                OLLAMA_MODELS.index(configured_model)
                if configured_model in OLLAMA_MODELS
                else 0
            )
            model = st.selectbox(
                "Model",
                OLLAMA_MODELS,
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
        with st.expander("Advanced"):
            main_tex = st.text_input(
                "Main .tex path",
                placeholder="Auto-detect",
                help="Only needed when the ZIP contains multiple LaTeX documents.",
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
            with st.spinner(
                "Tailoring and compiling your CV. Model and LaTeX runs can take a few minutes…"
            ):
                st.session_state["tailored_files"] = tailor_uploaded_cv(
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
                )
        except (
            ArchiveError,
            OpenAIError,
            ProviderConfigError,
            RuntimeError,
            UnicodeDecodeError,
            OSError,
            ValueError,
        ) as exc:
            st.session_state.pop("tailored_files", None)
            st.error(str(exc))

    output = st.session_state.get("tailored_files")
    if output is not None:
        st.success("Your tailored CV is ready.")
        st.caption(
            f"{output.provider} · {output.model} · {output.engine} · {output.main_tex}"
        )
        pdf_column, zip_column = st.columns(2)
        with pdf_column:
            st.download_button(
                "Download PDF",
                output.pdf,
                file_name="tailored_cv.pdf",
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
