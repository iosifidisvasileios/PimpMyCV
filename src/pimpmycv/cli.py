from __future__ import annotations

import argparse
import os
from pathlib import Path

from .agent import tailor_cv
from .compiler import SUPPORTED_ENGINES, find_engine
from .providers import PROVIDERS, ProviderConfigError, create_backend


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pimpmycv",
        description="Tailor a LaTeX CV to a job description and compile it to PDF.",
    )
    parser.add_argument("--cv", type=Path, required=True, help="Input .tex CV")
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
    if cv_path.suffix.lower() != ".tex":
        raise SystemExit("The CV must be a .tex file.")
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

    output_tex = output_dir / "tailored_cv.tex"
    print(
        f"Using {backend.provider} model {backend.model} and {selected_engine}..."
    )
    try:
        result = tailor_cv(
            backend.client,
            cv_tex=cv_path.read_text(encoding="utf-8"),
            job_description=job_path.read_text(encoding="utf-8"),
            output_tex=output_tex,
            source_dir=cv_path.parent,
            model=backend.model,
            engine=selected_engine,
            max_attempts=args.max_attempts,
            supports_stateful_responses=backend.supports_stateful_responses,
            response_options=backend.response_options,
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    print(f"LaTeX: {output_tex}")
    print(f"PDF:   {result.pdf_path}")
