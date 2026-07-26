from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from pimpmycv import gui
from pimpmycv.compiler import CompileResult


def _cv_zip() -> bytes:
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(
            "cv.tex",
            "\\documentclass{article}\\begin{document}Original\\end{document}",
        )
    return archive.getvalue()


def test_tailor_uploaded_cv_returns_downloadable_files(monkeypatch):
    monkeypatch.setattr(gui, "find_engine", lambda engine: "pdflatex")
    monkeypatch.setattr(
        gui,
        "create_backend",
        lambda *args, **kwargs: SimpleNamespace(
            provider="openai",
            model="test-model",
        ),
    )

    def fake_tailor(backend, **kwargs):
        pdf_path = Path(kwargs["output_tex"]).with_suffix(".pdf")
        pdf_path.write_bytes(b"%PDF-1.4\n")
        return CompileResult(True, "pdflatex", pdf_path, "ok")

    monkeypatch.setattr(gui, "tailor_cv", fake_tailor)

    output = gui.tailor_uploaded_cv(
        cv_zip=_cv_zip(),
        job_description="Python engineer",
        instructions="Keep it concise.",
    )

    assert output.pdf.startswith(b"%PDF")
    assert output.project_zip.startswith(b"PK")
    assert output.main_tex == "cv.tex"
    assert output.model == "test-model"


def test_ollama_gui_model_options_are_fixed():
    assert gui.OLLAMA_MODELS == ("gemma4:26b", "gemma4:e4b")


@pytest.mark.parametrize(
    ("cv_zip", "job_description", "message"),
    [
        (b"", "Python engineer", "non-empty CV"),
        (_cv_zip(), " ", "job description"),
    ],
)
def test_tailor_uploaded_cv_validates_browser_inputs(
    cv_zip,
    job_description,
    message,
):
    with pytest.raises(ValueError, match=message):
        gui.tailor_uploaded_cv(
            cv_zip=cv_zip,
            job_description=job_description,
        )
