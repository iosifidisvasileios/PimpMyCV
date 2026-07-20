import subprocess
from pathlib import Path

import pytest

from pimpmycv import compiler


def test_find_engine_reports_missing_install(monkeypatch):
    monkeypatch.setattr(compiler.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="No LaTeX compiler found"):
        compiler.find_engine()


def test_find_engine_prefers_pdflatex(monkeypatch):
    monkeypatch.setattr(
        compiler.shutil,
        "which",
        lambda name: f"/bin/{name}" if name in {"pdflatex", "tectonic"} else None,
    )
    assert compiler.find_engine() == "pdflatex"


def test_find_engine_prefers_latexmk(monkeypatch):
    monkeypatch.setattr(
        compiler.shutil,
        "which",
        lambda name: f"/bin/{name}" if name in {"latexmk", "pdflatex"} else None,
    )
    assert compiler.find_engine() == "latexmk"


def test_latexmk_uses_force_workflow_and_accepts_generated_pdf(
    monkeypatch,
    tmp_path: Path,
):
    tex = tmp_path / "main.tex"
    tex.write_text("\\documentclass{article}", encoding="utf-8")
    monkeypatch.setattr(compiler, "find_engine", lambda _: "latexmk")

    def run(command, **kwargs):
        assert command == [
            "latexmk",
            "-pdf",
            "-interaction=nonstopmode",
            "-file-line-error",
            "-f",
            "main.tex",
        ]
        assert kwargs["cwd"] == tmp_path.resolve()
        tex.with_suffix(".pdf").write_bytes(b"%PDF-generated")
        return subprocess.CompletedProcess(command, 12, "latex error", "")

    monkeypatch.setattr(compiler.subprocess, "run", run)

    result = compiler.compile_latex(tex, source_dir=tmp_path)

    assert result.success
    assert result.engine == "latexmk"
    assert "produced a non-empty PDF" in result.log


def test_latexmk_failure_without_pdf_is_not_accepted(monkeypatch, tmp_path: Path):
    tex = tmp_path / "main.tex"
    tex.write_text("broken", encoding="utf-8")
    monkeypatch.setattr(compiler, "find_engine", lambda _: "latexmk")
    monkeypatch.setattr(
        compiler.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            12,
            "fatal error",
            "",
        ),
    )

    result = compiler.compile_latex(tex, source_dir=tmp_path)

    assert not result.success
    assert not result.pdf_path.exists()


def test_compile_timeout_handles_byte_output(monkeypatch, tmp_path: Path):
    tex = tmp_path / "cv.tex"
    tex.write_text("\\documentclass{article}", encoding="utf-8")
    monkeypatch.setattr(compiler, "find_engine", lambda _: "pdflatex")

    def time_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(
            cmd="pdflatex",
            timeout=60,
            output=b"partial output",
            stderr=b"compiler stalled",
        )

    monkeypatch.setattr(compiler.subprocess, "run", time_out)

    result = compiler.compile_latex(tex, source_dir=tmp_path)

    assert not result.success
    assert "partial output" in result.log
    assert "compiler stalled" in result.log
