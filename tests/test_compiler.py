import subprocess
from pathlib import Path

import pytest

from pimpmycv import compiler


def test_find_engine_reports_missing_install(monkeypatch):
    monkeypatch.setattr(compiler.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="No LaTeX engine found"):
        compiler.find_engine()


def test_find_engine_prefers_pdflatex(monkeypatch):
    monkeypatch.setattr(
        compiler.shutil,
        "which",
        lambda name: f"/bin/{name}" if name in {"pdflatex", "tectonic"} else None,
    )
    assert compiler.find_engine() == "pdflatex"


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
