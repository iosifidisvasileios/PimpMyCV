from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import shlex
import shutil
import subprocess


SUPPORTED_ENGINES = ("latexmk", "pdflatex", "xelatex", "lualatex", "tectonic")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompileResult:
    success: bool
    engine: str
    pdf_path: Path
    log: str


def find_engine(requested: str = "auto") -> str:
    """Return an available LaTeX engine or raise a useful error."""
    logger.debug("[COMPILER] find_engine() called with requested=%s", requested)
    if requested != "auto":
        if requested not in SUPPORTED_ENGINES:
            choices = ", ".join(("auto", *SUPPORTED_ENGINES))
            raise ValueError(f"Unknown engine {requested!r}. Choose one of: {choices}.")
        if not shutil.which(requested):
            raise RuntimeError(f"LaTeX engine {requested!r} was not found on PATH.")
        logger.debug("[COMPILER] Using requested engine: %s", requested)
        return requested

    for engine in SUPPORTED_ENGINES:
        if shutil.which(engine):
            logger.debug("[COMPILER] Auto-detected engine: %s", engine)
            return engine
    raise RuntimeError(
        "No LaTeX compiler found. Install latexmk, MiKTeX/TeX Live "
        "(pdflatex, xelatex, or lualatex), or Tectonic, then make it "
        "available on PATH."
    )


def compile_latex(
    tex_path: Path,
    *,
    source_dir: Path,
    engine: str = "auto",
    timeout_seconds: int = 60,
) -> CompileResult:
    """Compile ``tex_path`` while resolving relative assets from ``source_dir``."""
    logger.debug("[COMPILER] compile_latex() called - tex_path=%s, source_dir=%s, engine=%s, timeout=%d", tex_path, source_dir, engine, timeout_seconds)
    tex_path = tex_path.resolve()
    source_dir = source_dir.resolve()
    tex_path.parent.mkdir(parents=True, exist_ok=True)
    selected = find_engine(engine)
    pdf_path = tex_path.with_suffix(".pdf")
    logger.debug("[COMPILER] Resolved paths - tex_path=%s, source_dir=%s, pdf_path=%s", tex_path, source_dir, pdf_path)
    if pdf_path.exists():
        logger.debug("[COMPILER] Removing existing PDF: %s", pdf_path)
        pdf_path.unlink()

    if selected == "latexmk":
        commands = [[
            selected,
            "-pdf",
            "-interaction=nonstopmode",
            "-file-line-error",
            "-f",
            tex_path.name,
        ]]
        logger.debug("[COMPILER] Using latexmk command (1 pass)")
    elif selected == "tectonic":
        commands = [[
            selected,
            "--untrusted",
            "--keep-logs",
            "--outdir",
            str(tex_path.parent),
            str(tex_path),
        ]]
        logger.debug("[COMPILER] Using tectonic command (1 pass)")
    else:
        base = [
            selected,
            "-no-shell-escape",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            f"-output-directory={tex_path.parent}",
            str(tex_path),
        ]
        # Two passes settle common references and page counts.
        commands = [base, base]
        logger.debug("[COMPILER] Using %s command (2 passes)", selected)

    log_parts: list[str] = []
    pass_number = 0
    try:
        for command in commands:
            pass_number += 1
            logger.info("Running compiler: %s", shlex.join(command))
            logger.debug("[COMPILER] Pass %d/%d, working directory: %s", pass_number, len(commands), source_dir)
            process = subprocess.run(
                command,
                cwd=source_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
            logger.debug("[COMPILER] Pass %d exit code: %d", pass_number, process.returncode)
            stdout_len = len(process.stdout) if process.stdout else 0
            stderr_len = len(process.stderr) if process.stderr else 0
            logger.debug("[COMPILER] Pass %d output - stdout=%d chars, stderr=%d chars", pass_number, stdout_len, stderr_len)
            log_parts.extend(part for part in (process.stdout, process.stderr) if part)
            if process.returncode != 0:
                # With -f, latexmk can produce a usable PDF while reporting
                # recoverable errors from the source document.
                if (
                    selected == "latexmk"
                    and pdf_path.is_file()
                    and pdf_path.stat().st_size > 0
                ):
                    log_parts.append(
                        "latexmk reported errors but produced a non-empty PDF."
                    )
                    logger.warning(
                        "latexmk reported errors but generated a non-empty PDF."
                    )
                    logger.debug("[COMPILER] PDF size: %d bytes", pdf_path.stat().st_size)
                    continue
                logger.warning("Compilation failed with exit code %d.", process.returncode)
                return CompileResult(False, selected, pdf_path, "\n".join(log_parts))
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(
            part.decode("utf-8", errors="replace")
            if isinstance(part, bytes)
            else part
            for part in (exc.stdout, exc.stderr)
            if part
        )
        logger.warning("Compilation timed out after %d seconds.", timeout_seconds)
        logger.debug("[COMPILER] Timeout output: %s", output[:500])
        log_parts.append(f"Compilation timed out after {timeout_seconds} seconds.\n{output}")
        return CompileResult(False, selected, pdf_path, "\n".join(log_parts))

    success = pdf_path.is_file() and pdf_path.stat().st_size > 0
    if not success:
        log_parts.append("The compiler exited successfully but did not create a PDF.")
        logger.warning("Compiler exited without creating a non-empty PDF.")
    else:
        logger.info("Compiler created: %s", pdf_path)
        logger.debug("[COMPILER] PDF size: %d bytes", pdf_path.stat().st_size)
    logger.debug("[COMPILER] Compilation complete - success=%s, engine=%s, log_length=%d", success, selected, len("\n".join(log_parts)))
    return CompileResult(success, selected, pdf_path, "\n".join(log_parts))
