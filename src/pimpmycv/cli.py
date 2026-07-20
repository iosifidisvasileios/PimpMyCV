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
    parser.add_argument("--job", type=Path, required=True, help="Job description text file")
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
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    cv_path = args.cv.expanduser().resolve()
    job_path = args.job.expanduser().resolve()
    output_dir = args.output.expanduser().resolve()

    if not cv_path.is_file():
        raise SystemExit(f"CV file not found: {cv_path}")
    if cv_path.suffix.lower() != ".zip":
        raise SystemExit("The CV project must be a .zip file.")
    if not job_path.is_file():
        raise SystemExit(f"Job description not found: {job_path}")
    if args.max_attempts < 1:
        raise SystemExit("--max-attempts must be at least 1.")

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
    print(
        f"Using {backend.provider} model {backend.model} and {selected_engine}..."
    )
    try:
        with extract_cv_archive(cv_path, args.main_tex) as project:
            result = tailor_cv(
                backend.client,
                cv_tex=project.main_tex.read_text(encoding="utf-8"),
                job_description=job_path.read_text(encoding="utf-8"),
                output_tex=project.main_tex,
                source_dir=project.main_tex.parent,
                model=backend.model,
                engine=selected_engine,
                max_attempts=args.max_attempts,
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
