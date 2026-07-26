"""Temporary, safe inspection of ZIP Python-project uploads."""
from pathlib import Path, PurePosixPath
import tempfile
import zipfile

from fastapi import HTTPException, UploadFile

from services import app_project_detector


async def inspect_zip(upload: UploadFile) -> dict[str, object]:
    if not upload.filename or not upload.filename.lower().endswith(".zip"):
        raise HTTPException(400, "Upload a ZIP file.")
    data = await upload.read()
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(400, "ZIP is larger than 100 MB.")
    try:
        with tempfile.TemporaryDirectory(prefix="srv-panel-zip-") as temp_dir:
            archive = Path(temp_dir) / "project.zip"
            archive.write_bytes(data)
            source = Path(temp_dir) / "source"
            with zipfile.ZipFile(archive) as zip_file:
                _validate_archive(zip_file)
                zip_file.extractall(source)
            result = app_project_detector.detect_project(source)
    except zipfile.BadZipFile as exc:
        raise HTTPException(400, "The uploaded ZIP is not valid.") from exc
    await upload.seek(0)
    return result


def _validate_archive(archive: zipfile.ZipFile) -> None:
    if len(archive.infolist()) > 1000:
        raise HTTPException(400, "ZIP has too many files.")
    for item in archive.infolist():
        path = PurePosixPath(item.filename)
        is_link = (item.external_attr >> 16) & 0o170000 == 0o120000
        if path.is_absolute() or ".." in path.parts or is_link:
            raise HTTPException(400, "ZIP contains an unsafe path.")
