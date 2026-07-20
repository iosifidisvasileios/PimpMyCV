from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil

from .agent import tailor_cv
from .archive import ArchiveError, extract_cv_archive, write_tailored_archive
from .compiler import SUPPORTED_ENGINES, find_engine
from .providers import PROVIDERS, ProviderConfigError, create_backend


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pimpmycv",
        description="Tailor a LaTeX CV to a job description and compile it to PDF.",
    )
    parser.add_argument(
        "--cv",
        type=Path,
        required=True,
        help="ZIP containing the LaTeX CV and its support files",
    )
    parser.add_argument(
        "--main-tex",
        help="Path to the main .tex file inside the ZIP (auto-detected by default)",
    )
    parser.add_argument(
        "--job",
        type=Path,
        required=True,
        help="UTF-8 .txt job description",
    )
    parser.add_argument(
        "--provider",
        choices=PROVIDERS,
        default=os.getenv("PIMPMYCV_PROVIDER", "openai"),
        help="Model provider: openai, azure, or ollama (default: openai)",
    )
    parser.add_argument(
        "--endpoint",
        help="Override the provider endpoint/base URL",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("build"),
        help="Output directory (default: build)",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("PIMPMYCV_MODEL"),
        help="Model ID, or Azure deployment name",
    )
    parser.add_argument(
        "--engine",
        choices=("auto", *SUPPORTED_ENGINES),
        default="auto",
        help="LaTeX engine (default: auto)",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=4,
        help="Maximum compile/fix attempts (default: 4)",
    )
    parser.add_argument(
        "--max-feedback-rounds",
        type=int,
        default=5,
        help="Maximum user-requested revisions (default: 5)",
    )
    parser.add_argument(
        "--no-feedback",
        action="store_true",
        help="Skip interactive draft review and accept the first compilable CV",
    )
    return parser


def validate_input_paths(cv_path: Path, job_path: Path) -> None:
    """Validate the two user-supplied input artifacts before any API work."""
    if not cv_path.is_file():
        raise ValueError(f"CV file not found: {cv_path}")
    if cv_path.suffix.lower() != ".zip":
        raise ValueError("The CV project must be a .zip file.")
    if not job_path.is_file():
        raise ValueError(f"Job description not found: {job_path}")
    if job_path.suffix.lower() != ".txt":
        raise ValueError("The job description must be a .txt file.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    cv_path = args.cv.expanduser().resolve()
    job_path = args.job.expanduser().resolve()
    output_dir = args.output.expanduser().resolve()

    try:
        validate_input_paths(cv_path, job_path)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.max_attempts < 1:
        raise SystemExit("--max-attempts must be at least 1.")
    if args.max_feedback_rounds < 0:
        raise SystemExit("--max-feedback-rounds cannot be negative.")

    try:
        selected_engine = find_engine(args.engine)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    try:
        backend = create_backend(
            args.provider,
            model=args.model,
            endpoint=args.endpoint,
        )
    except ProviderConfigError as exc:
        raise SystemExit(str(exc)) from exc

    output_pdf = output_dir / "tailored_cv.pdf"
    output_zip = output_dir / "tailored_cv.zip"
    draft_pdf = output_dir / "draft_cv.pdf"
    draft_zip = output_dir / "draft_cv.zip"
    print(
        f"Using {backend.provider} model {backend.model} and {selected_engine}..."
    )
    try:
        with extract_cv_archive(cv_path, args.main_tex) as project:
            def request_feedback(result, summary: str, draft_number: int) -> str | None:
                output_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(result.pdf_path, draft_pdf)
                write_tailored_archive(project, draft_zip, pdf_path=result.pdf_path)
                print(f"\nDraft {draft_number} is ready:")
                print(f"  PDF:     {draft_pdf}")
                print(f"  Project: {draft_zip}")
                print("\nAgent's rewrite summary:")
                print(summary)
                try:
                    feedback = input(
                        "\nEnter feedback for another revision, or press Enter to accept: "
                    )
                except EOFError:
                    return None
                return feedback.strip() or None

            result = tailor_cv(
                backend.client,
                cv_tex=project.main_tex.read_text(encoding="utf-8"),
                job_description=job_path.read_text(encoding="utf-8"),
                output_tex=project.main_tex,
                source_dir=project.main_tex.parent,
                model=backend.model,
                engine=selected_engine,
                max_attempts=args.max_attempts,
                max_feedback_rounds=args.max_feedback_rounds,
                feedback_callback=None if args.no_feedback else request_feedback,
                supports_stateful_responses=backend.supports_stateful_responses,
                response_options=backend.response_options,
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(result.pdf_path, output_pdf)
            write_tailored_archive(project, output_zip, pdf_path=result.pdf_path)
    except (ArchiveError, RuntimeError, UnicodeDecodeError) as exc:
        raise SystemExit(str(exc)) from exc

    print(f"Project: {output_zip}")
    print(f"PDF:     {output_pdf}")
