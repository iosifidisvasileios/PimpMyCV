from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import logging
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from typing import Iterator
import zipfile


logger = logging.getLogger(__name__)


MAX_FILES = 1_000
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024


class ArchiveError(ValueError):
    """Raised when a CV archive is unsafe or does not identify a main document."""


@dataclass(frozen=True)
class CVProject:
    root: Path
    main_tex: Path
    members: tuple[Path, ...]

    @property
    def main_relative_path(self) -> Path:
        return self.main_tex.relative_to(self.root)


def _safe_relative_path(name: str) -> Path:
    # logger.debug("[ARCHIVE] _safe_relative_path() called with name=%s", name)
    path = PurePosixPath(name.replace("\\", "/"))
    if (
        not name
        or path.is_absolute()
        or ".." in path.parts
        or (path.parts and ":" in path.parts[0])
    ):
        raise ArchiveError(f"Unsafe path in CV archive: {name!r}")
    result = Path(*path.parts)
    # logger.debug("[ARCHIVE] Safe relative path: %s", result)
    return result


def _choose_main_tex(root: Path, members: tuple[Path, ...], requested: str | None) -> Path:
    logger.debug("[ARCHIVE] _choose_main_tex() called - requested=%s, total_members=%d", requested, len(members))
    tex_files = [path for path in members if path.suffix.lower() == ".tex"]
    logger.debug("[ARCHIVE] Found %d .tex files", len(tex_files))
    if requested:
        logger.debug("[ARCHIVE] Using requested main tex: %s", requested)
        relative = _safe_relative_path(requested)
        candidate = root / relative
        if relative not in members or not candidate.is_file():
            raise ArchiveError(f"Main LaTeX file not found in archive: {requested}")
        if candidate.suffix.lower() != ".tex":
            raise ArchiveError("--main-tex must point to a .tex file.")
        logger.debug("[ARCHIVE] Selected main tex: %s", candidate)
        return candidate

    documents = []
    for relative in tex_files:
        text = (root / relative).read_text(encoding="utf-8", errors="ignore")
        if "\\documentclass" in text:
            documents.append(relative)
    logger.debug("[ARCHIVE] Found %d documents with \\documentclass", len(documents))

    if len(documents) == 1:
        selected = root / documents[0]
        logger.debug("[ARCHIVE] Auto-selected single document: %s", selected)
        return selected
    if not documents and len(tex_files) == 1:
        selected = root / tex_files[0]
        logger.debug("[ARCHIVE] Auto-selected single tex file: %s", selected)
        return selected

    candidates = documents or tex_files
    if not candidates:
        raise ArchiveError("The CV archive does not contain a .tex file.")
    names = ", ".join(path.as_posix() for path in candidates)
    raise ArchiveError(
        f"Could not identify one main LaTeX file ({names}). Pass --main-tex PATH."
    )


@contextmanager
def extract_cv_archive(archive_path: Path, main_tex: str | None = None) -> Iterator[CVProject]:
    """Safely extract a CV project ZIP into a temporary working directory."""
    logger.debug("[ARCHIVE] extract_cv_archive() called - archive_path=%s, main_tex=%s", archive_path, main_tex)
    try:
        archive = zipfile.ZipFile(archive_path)
        logger.debug("[ARCHIVE] ZIP archive opened successfully")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ArchiveError(f"Invalid CV ZIP archive: {archive_path}") from exc

    with archive, tempfile.TemporaryDirectory(prefix="pimpmycv-") as temp_dir:
        root = Path(temp_dir).resolve()
        logger.debug("[ARCHIVE] Temporary directory created: %s", root)
        infos = archive.infolist()
        files = [info for info in infos if not info.is_dir()]
        logger.debug("[ARCHIVE] Archive contains %d files, %d directories", len(files), len(infos) - len(files))
        if len(files) > MAX_FILES:
            raise ArchiveError(f"CV archive contains more than {MAX_FILES} files.")
        total_size = sum(info.file_size for info in files)
        logger.debug("[ARCHIVE] Total uncompressed size: %d bytes", total_size)
        if total_size > MAX_UNCOMPRESSED_BYTES:
            raise ArchiveError("CV archive exceeds the 100 MB uncompressed limit.")

        members: list[Path] = []
        seen: set[Path] = set()
        for info in infos:
            relative = _safe_relative_path(info.filename)
            if relative in seen:
                raise ArchiveError(f"Duplicate path in CV archive: {info.filename}")
            seen.add(relative)

            file_type = (info.external_attr >> 16) & 0o170000
            if file_type == stat.S_IFLNK:
                raise ArchiveError(f"Symbolic links are not allowed: {info.filename}")

            target = (root / relative).resolve()
            if target != root and root not in target.parents:
                raise ArchiveError(f"Unsafe path in CV archive: {info.filename!r}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                logger.debug("[ARCHIVE] Created directory: %s", relative)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            members.append(relative)
            logger.debug("[ARCHIVE] Extracted file: %s (%d bytes)", relative, info.file_size)

        member_tuple = tuple(members)
        logger.debug("[ARCHIVE] Extraction complete - %d members extracted", len(member_tuple))
        selected = _choose_main_tex(root, member_tuple, main_tex)
        logger.debug("[ARCHIVE] Yielding CVProject - root=%s, main_tex=%s", root, selected)
        yield CVProject(root=root, main_tex=selected, members=member_tuple)


def write_tailored_archive(
    project: CVProject,
    destination: Path,
    *,
    pdf_path: Path,
) -> None:
    """Package the rewritten project, its original support files, and generated PDF."""
    logger.debug("[ARCHIVE] write_tailored_archive() called - destination=%s, pdf_path=%s", destination, pdf_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pdf_relative = project.main_relative_path.with_suffix(".pdf")
    files = {relative: project.root / relative for relative in project.members}
    files[pdf_relative] = pdf_path
    logger.debug("[ARCHIVE] Packaging %d files into archive", len(files))

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, source in sorted(files.items(), key=lambda item: item[0].as_posix()):
            archive.write(source, relative.as_posix())
            logger.debug("[ARCHIVE] Added to archive: %s", relative)
    logger.debug("[ARCHIVE] Archive written successfully: %s", destination)
