from pathlib import Path

import pytest

from pimpmycv.cli import build_parser, validate_input_paths


def test_accepts_zip_cv_and_txt_job_description(tmp_path: Path):
    cv = tmp_path / "cv.zip"
    job = tmp_path / "job.txt"
    instructions = tmp_path / "instructions.md"
    cv.write_bytes(b"zip placeholder")
    job.write_text("Python engineer", encoding="utf-8")
    instructions.write_text("Keep it concise.", encoding="utf-8")

    validate_input_paths(cv, job, instructions)


def test_rejects_non_txt_job_description(tmp_path: Path):
    cv = tmp_path / "cv.zip"
    job = tmp_path / "job.md"
    cv.write_bytes(b"zip placeholder")
    job.write_text("Python engineer", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a .txt file"):
        validate_input_paths(cv, job)


def test_rejects_unsupported_instructions_file(tmp_path: Path):
    cv = tmp_path / "cv.zip"
    job = tmp_path / "job.txt"
    instructions = tmp_path / "instructions.json"
    cv.write_bytes(b"zip placeholder")
    job.write_text("Python engineer", encoding="utf-8")
    instructions.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a .txt or .md file"):
        validate_input_paths(cv, job, instructions)


def test_parser_accepts_verbose_and_debug_flags():
    args = build_parser().parse_args([
        "--cv",
        "cv.zip",
        "--job",
        "job.txt",
        "--verbose",
        "--debug",
    ])

    assert args.verbose
    assert args.debug
