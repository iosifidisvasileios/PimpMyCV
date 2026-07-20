from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess


SUPPORTED_ENGINES = ("pdflatex", "xelatex", "lualatex", "tectonic")


@dataclass(frozen=True)
class CompileResult:
    success: bool
    engine: str
    pdf_path: Path
    log: str


def find_engine(requested: str = "auto") -> str:
    """Return an available LaTeX engine or raise a useful error."""
    if requested != "auto":
        if requested not in SUPPORTED_ENGINES:
            choices = ", ".join(("auto", *SUPPORTED_ENGINES))
            raise ValueError(f"Unknown engine {requested!r}. Choose one of: {choices}.")
        if not shutil.which(requested):
            raise RuntimeError(f"LaTeX engine {requested!r} was not found on PATH.")
        return requested

    for engine in SUPPORTED_ENGINES:
        if shutil.which(engine):
            return engine
    raise RuntimeError(
        "No LaTeX engine found. Install MiKTeX/TeX Live (pdflatex, xelatex, "
        "or lualatex) or Tectonic, then make it available on PATH."
    )


def compile_latex(
    tex_path: Path,
    *,
    source_dir: Path,
    engine: str = "auto",
    timeout_seconds: int = 60,
) -> CompileResult:
    """Compile ``tex_path`` while resolving relative assets from ``source_dir``."""
    tex_path = tex_path.resolve()
    source_dir = source_dir.resolve()
    tex_path.parent.mkdir(parents=True, exist_ok=True)
    selected = find_engine(engine)
    pdf_path = tex_path.with_suffix(".pdf")
    if pdf_path.exists():
        pdf_path.unlink()

    if selected == "tectonic":
        commands = [[
            selected,
            "--untrusted",
            "--keep-logs",
            "--outdir",
            str(tex_path.parent),
            str(tex_path),
        ]]
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

    log_parts: list[str] = []
    try:
        for command in commands:
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
            log_parts.extend(part for part in (process.stdout, process.stderr) if part)
            if process.returncode != 0:
                return CompileResult(False, selected, pdf_path, "\n".join(log_parts))
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + "\n" + (exc.stderr or "")
        log_parts.append(f"Compilation timed out after {timeout_seconds} seconds.\n{output}")
        return CompileResult(False, selected, pdf_path, "\n".join(log_parts))

    success = pdf_path.is_file() and pdf_path.stat().st_size > 0
    if not success:
        log_parts.append("The compiler exited successfully but did not create a PDF.")
    return CompileResult(success, selected, pdf_path, "\n".join(log_parts))
