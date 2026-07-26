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

    captured_options = {}

    def fake_tailor(backend, **kwargs):
        captured_options.update(kwargs)
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
    assert captured_options["max_feedback_rounds"] == 5
    assert captured_options["feedback_callback"] is None


def test_gui_job_pauses_for_feedback_and_completes(monkeypatch, tmp_path: Path):
    draft_pdf = tmp_path / "draft.pdf"
    draft_pdf.write_bytes(b"%PDF-draft")

    def fake_runner(**kwargs):
        feedback = kwargs["feedback_callback"](
            CompileResult(True, "pdflatex", draft_pdf, "ok"),
            "Reordered the experience section.",
            1,
        )
        assert feedback == "Shorten the summary."
        return gui.TailoredFiles(
            pdf=b"%PDF-final",
            project_zip=b"PK-final",
            engine="pdflatex",
            provider="ollama",
            model="gemma4:26b",
            main_tex="cv.tex",
        )

    monkeypatch.setattr(gui, "tailor_uploaded_cv", fake_runner)
    job = gui.GuiTailoringJob(cv_zip=b"zip").start()

    draft_event = job.events.get(timeout=2)
    assert draft_event.kind == "draft"
    assert draft_event.payload.pdf == b"%PDF-draft"
    assert draft_event.payload.number == 1
    assert job.awaiting_feedback

    job.submit_feedback("Shorten the summary.")
    completed_event = job.events.get(timeout=2)
    job.join(timeout=2)

    assert completed_event.kind == "complete"
    assert completed_event.payload.pdf == b"%PDF-final"
    assert not job.running


def test_gui_job_accepts_current_draft(monkeypatch, tmp_path: Path):
    draft_pdf = tmp_path / "draft.pdf"
    draft_pdf.write_bytes(b"%PDF-draft")

    def fake_runner(**kwargs):
        feedback = kwargs["feedback_callback"](
            CompileResult(True, "pdflatex", draft_pdf, "ok"),
            "Focused the skills section.",
            1,
        )
        assert feedback is None
        return gui.TailoredFiles(
            pdf=b"%PDF-accepted",
            project_zip=b"PK-accepted",
            engine="pdflatex",
            provider="openai",
            model="test-model",
            main_tex="cv.tex",
        )

    monkeypatch.setattr(gui, "tailor_uploaded_cv", fake_runner)
    job = gui.GuiTailoringJob(cv_zip=b"zip").start()

    assert job.events.get(timeout=2).kind == "draft"
    job.submit_feedback(None)
    completed_event = job.events.get(timeout=2)
    job.join(timeout=2)

    assert completed_event.kind == "complete"
    assert completed_event.payload.pdf == b"%PDF-accepted"


def test_ollama_gui_model_options_are_fixed():
    assert gui.OLLAMA_MODELS == ("gemma4:26b", "gemma4:e4b")


def test_pdf_embed_uses_an_inline_data_url():
    data_url = gui._pdf_data_url(b"%PDF-test")

    assert data_url == "data:application/pdf;base64,JVBERi10ZXN0"
    assert "%PDF-test" not in data_url


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
