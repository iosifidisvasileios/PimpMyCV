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
