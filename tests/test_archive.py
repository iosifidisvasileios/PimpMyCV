from pathlib import Path
import zipfile

import pytest

from pimpmycv.archive import ArchiveError, extract_cv_archive, write_tailored_archive


def _make_zip(path: Path, files: dict[str, str | bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def test_extracts_project_and_detects_documentclass(tmp_path: Path):
    source = tmp_path / "cv.zip"
    _make_zip(source, {
        "cv/main.tex": "\\documentclass{article}\\begin{document}CV\\end{document}",
        "cv/sections.tex": "Support text",
        "assets/photo.png": b"PNG",
    })

    with extract_cv_archive(source) as project:
        assert project.main_relative_path == Path("cv/main.tex")
        assert (project.root / "assets/photo.png").read_bytes() == b"PNG"


def test_rejects_archive_path_traversal(tmp_path: Path):
    source = tmp_path / "unsafe.zip"
    _make_zip(source, {
        "../outside.tex": "\\documentclass{article}",
    })

    with pytest.raises(ArchiveError, match="Unsafe path"):
        with extract_cv_archive(source):
            pass


def test_requires_main_tex_when_multiple_documents_exist(tmp_path: Path):
    source = tmp_path / "ambiguous.zip"
    _make_zip(source, {
        "one.tex": "\\documentclass{article}",
        "two.tex": "\\documentclass{article}",
    })

    with pytest.raises(ArchiveError, match="--main-tex"):
        with extract_cv_archive(source):
            pass

    with extract_cv_archive(source, "two.tex") as project:
        assert project.main_relative_path == Path("two.tex")


def test_tailored_archive_preserves_support_files_and_adds_pdf(tmp_path: Path):
    source = tmp_path / "cv.zip"
    output = tmp_path / "tailored_cv.zip"
    _make_zip(source, {
        "main.tex": "\\documentclass{article} Original",
        "cvstyle.sty": "style contents",
    })

    with extract_cv_archive(source) as project:
        project.main_tex.write_text("\\documentclass{article} Tailored", encoding="utf-8")
        pdf = project.main_tex.with_suffix(".pdf")
        pdf.write_bytes(b"%PDF-generated")
        write_tailored_archive(project, output, pdf_path=pdf)

    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {"main.tex", "main.pdf", "cvstyle.sty"}
        assert b"Tailored" in archive.read("main.tex")
        assert archive.read("cvstyle.sty") == b"style contents"
        assert archive.read("main.pdf") == b"%PDF-generated"
