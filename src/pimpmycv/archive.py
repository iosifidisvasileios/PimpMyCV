from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from typing import Iterator
import zipfile


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
    path = PurePosixPath(name.replace("\\", "/"))
    if (
        not name
        or path.is_absolute()
        or ".." in path.parts
        or (path.parts and ":" in path.parts[0])
    ):
        raise ArchiveError(f"Unsafe path in CV archive: {name!r}")
    return Path(*path.parts)


def _choose_main_tex(root: Path, members: tuple[Path, ...], requested: str | None) -> Path:
    tex_files = [path for path in members if path.suffix.lower() == ".tex"]
    if requested:
        relative = _safe_relative_path(requested)
        candidate = root / relative
        if relative not in members or not candidate.is_file():
            raise ArchiveError(f"Main LaTeX file not found in archive: {requested}")
        if candidate.suffix.lower() != ".tex":
            raise ArchiveError("--main-tex must point to a .tex file.")
        return candidate

    documents = []
    for relative in tex_files:
        text = (root / relative).read_text(encoding="utf-8", errors="ignore")
        if "\\documentclass" in text:
            documents.append(relative)

    if len(documents) == 1:
        return root / documents[0]
    if not documents and len(tex_files) == 1:
        return root / tex_files[0]

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
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ArchiveError(f"Invalid CV ZIP archive: {archive_path}") from exc

    with archive, tempfile.TemporaryDirectory(prefix="pimpmycv-") as temp_dir:
        root = Path(temp_dir).resolve()
        infos = archive.infolist()
        files = [info for info in infos if not info.is_dir()]
        if len(files) > MAX_FILES:
            raise ArchiveError(f"CV archive contains more than {MAX_FILES} files.")
        if sum(info.file_size for info in files) > MAX_UNCOMPRESSED_BYTES:
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
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            members.append(relative)

        member_tuple = tuple(members)
        selected = _choose_main_tex(root, member_tuple, main_tex)
        yield CVProject(root=root, main_tex=selected, members=member_tuple)


def write_tailored_archive(
    project: CVProject,
    destination: Path,
    *,
    pdf_path: Path,
) -> None:
    """Package the rewritten project, its original support files, and generated PDF."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    pdf_relative = project.main_relative_path.with_suffix(".pdf")
    files = {relative: project.root / relative for relative in project.members}
    files[pdf_relative] = pdf_path

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, source in sorted(files.items(), key=lambda item: item[0].as_posix()):
            archive.write(source, relative.as_posix())
