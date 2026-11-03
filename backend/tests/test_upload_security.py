import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile

from app.api import documents
from app.models.financial import User


def _upload(name: str, content: bytes):
    file = UploadFile(filename=name, file=io.BytesIO(content))
    admin = User(email="admin@example.com", role="admin")
    return asyncio.run(documents.upload_document(file=file, doc_type=None, db=None, user=admin))


def test_rejects_non_pdf_extension():
    with pytest.raises(HTTPException) as exc:
        _upload("report.exe", b"%PDF-1.4")
    assert exc.value.status_code == 400


def test_rejects_wrong_magic_bytes():
    with pytest.raises(HTTPException) as exc:
        _upload("report.pdf", b"MZ not a pdf")
    assert exc.value.status_code == 400


def test_rejects_oversized_file(monkeypatch):
    monkeypatch.setattr(documents, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(HTTPException) as exc:
        _upload("report.pdf", b"%PDF-" + b"0" * 100)
    assert exc.value.status_code == 413


def test_unparseable_pdf_is_removed(monkeypatch, tmp_path):
    monkeypatch.setattr(documents, "UPLOAD_DIR", tmp_path)
    with pytest.raises(HTTPException) as exc:
        _upload("../../evil.pdf", b"%PDF-1.4 garbage")
    assert exc.value.status_code == 400
    assert list(tmp_path.iterdir()) == []
