from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import shutil

from openai import OpenAIError

from .agent import tailor_cv
from .archive import ArchiveError, extract_cv_archive, write_tailored_archive
from .compiler import SUPPORTED_ENGINES, find_engine
from .providers import PROVIDERS, ProviderConfigError, create_backend


logger = logging.getLogger(__name__)


def configure_logging(*, verbose: bool, debug: bool) -> None:
    """Enable concise project logs without dumping SDK request bodies."""
    level = logging.DEBUG if debug else logging.INFO if verbose else logging.WARNING
    logging.basicConfig(level=level, format="[%(levelname)s] %(message)s")
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


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
        "--instructions",
        type=Path,
        help="Optional UTF-8 .txt or .md file with additional rewrite instructions",
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
        "--pdf",
        type=Path,
        default=None,
        help="Full path for output PDF (overrides --output and --pdf-name)",
    )
    parser.add_argument(
        "--pdf-name",
        default="tailored_cv.pdf",
        help="Output PDF filename (default: tailored_cv.pdf)",
    )
    parser.add_argument(
        "--model",
        default=None,
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
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show model, candidate, compiler, and feedback-loop progress",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show detailed progress and save attempts under OUTPUT/debug",
    )
    return parser


def validate_input_paths(
    cv_path: Path,
    job_path: Path,
    instructions_path: Path | None = None,
) -> None:
    """Validate user-supplied input files before any API work."""
    if not cv_path.is_file():
        raise ValueError(f"CV file not found: {cv_path}")
    if cv_path.suffix.lower() != ".zip":
        raise ValueError("The CV project must be a .zip file.")
    if not job_path.is_file():
        raise ValueError(f"Job description not found: {job_path}")
    if job_path.suffix.lower() != ".txt":
        raise ValueError("The job description must be a .txt file.")
    if instructions_path is not None:
        if not instructions_path.is_file():
            raise ValueError(f"Instructions file not found: {instructions_path}")
        if instructions_path.suffix.lower() not in {".txt", ".md"}:
            raise ValueError("The instructions must be a .txt or .md file.")


def main(argv: list[str] | None = None) -> None:
    logger.debug("[CLI] main() called with argv: %s", argv)
    args = build_parser().parse_args(argv)
    # Set model default based on provider if not specified
    if args.model is None:
        if args.provider == "ollama":
            args.model = os.getenv("OLLAMA_MODEL", "qwen3:8b")
        elif args.provider == "azure":
            args.model = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        else:
            args.model = os.getenv("PIMPMYCV_MODEL", "gpt-5.6-sol")
    logger.debug("[CLI] Parsed arguments: provider=%s, model=%s, engine=%s, cv=%s, job=%s", args.provider, args.model, args.engine, args.cv, args.job)
    configure_logging(verbose=args.verbose, debug=args.debug)
    logger.debug("[CLI] Logging configured - verbose=%s, debug=%s", args.verbose, args.debug)
    cv_path = args.cv.expanduser().resolve()
    job_path = args.job.expanduser().resolve()
    instructions_path = (
        args.instructions.expanduser().resolve() if args.instructions else None
    )
    output_dir = args.output.expanduser().resolve()

    try:
        logger.debug("[CLI] Validating input paths...")
        validate_input_paths(cv_path, job_path, instructions_path)
        logger.debug("[CLI] Input paths validated successfully")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.max_attempts < 1:
        raise SystemExit("--max-attempts must be at least 1.")
    if args.max_feedback_rounds < 0:
        raise SystemExit("--max-feedback-rounds cannot be negative.")

    try:
        logger.debug("[CLI] Finding LaTeX engine: %s", args.engine)
        selected_engine = find_engine(args.engine)
        logger.debug("[CLI] Selected LaTeX engine: %s", selected_engine)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    try:
        logger.debug("[CLI] Creating backend for provider: %s", args.provider)
        logger.debug("[CLI] Model from args: %s, PIMPMYCV_MODEL env: %s", args.model, os.getenv("PIMPMYCV_MODEL"))
        if args.provider == "ollama":
            logger.debug("[CLI] OLLAMA_MODEL env: %s", os.getenv("OLLAMA_MODEL"))
        backend = create_backend(
            args.provider,
            model=args.model,
            endpoint=args.endpoint,
        )
        logger.debug("[CLI] Backend created - provider=%s, model=%s, stateful=%s", backend.provider, backend.model, backend.supports_stateful_responses)
    except ProviderConfigError as exc:
        raise SystemExit(str(exc)) from exc

    if args.pdf:
        output_pdf = args.pdf.expanduser().resolve()
        output_dir = output_pdf.parent
    else:
        output_pdf = output_dir / args.pdf_name
    output_zip = output_dir / "tailored_cv.zip"
    draft_pdf = output_dir / "draft_cv.pdf"
    draft_zip = output_dir / "draft_cv.zip"
    print(
        f"Using {backend.provider} model {backend.model} and {selected_engine}..."
    )
    logger.info("CV archive: %s", cv_path)
    logger.info("Job description: %s", job_path)
    if args.debug:
        logger.warning(
            "Debug artifacts can contain CV and job-description content: %s",
            output_dir / "debug",
        )
    try:
        logger.debug("[CLI] Extracting CV archive: %s", cv_path)
        with extract_cv_archive(cv_path, args.main_tex) as project:
            logger.info("Main LaTeX document: %s", project.main_relative_path)
            logger.debug("[CLI] Archive extracted - root=%s, members=%d", project.root, len(project.members))
            def request_feedback(result, summary: str, draft_number: int) -> str | None:
                logger.debug("[CLI] request_feedback() called - draft_number=%d", draft_number)
                output_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(result.pdf_path, draft_pdf)
                write_tailored_archive(project, draft_zip, pdf_path=result.pdf_path)
                logger.debug("[CLI] Draft files written - PDF=%s, ZIP=%s", draft_pdf, draft_zip)
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

            logger.debug("[CLI] Calling tailor_cv() with max_attempts=%d, max_feedback_rounds=%d", args.max_attempts, args.max_feedback_rounds)
            result = tailor_cv(
                backend,
                cv_tex=project.main_tex.read_text(encoding="utf-8"),
                job_description=job_path.read_text(encoding="utf-8"),
                user_instructions=(
                    instructions_path.read_text(encoding="utf-8")
                    if instructions_path
                    else ""
                ),
                output_tex=project.main_tex,
                source_dir=project.main_tex.parent,
                engine=selected_engine,
                max_attempts=args.max_attempts,
                max_feedback_rounds=args.max_feedback_rounds,
                feedback_callback=None if args.no_feedback else request_feedback,
                debug_dir=output_dir / "debug" if args.debug else None,
            )
            logger.debug("[CLI] tailor_cv() completed successfully")
            logger.debug("[CLI] Writing final output files...")
            output_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(result.pdf_path, output_pdf)
            logger.debug("[CLI] Copied PDF to: %s", output_pdf)
            write_tailored_archive(project, output_zip, pdf_path=result.pdf_path)
            logger.debug("[CLI] Wrote archive to: %s", output_zip)
    except (
        ArchiveError,
        OpenAIError,
        RuntimeError,
        UnicodeDecodeError,
        OSError,
    ) as exc:
        raise SystemExit(str(exc)) from exc

    print(f"Project: {output_zip}")
    print(f"PDF:     {output_pdf}")
