"""Phase 3: PDF parsing at upload time -> extracted .txt sibling."""

import io
import os
import tempfile
from pathlib import Path

os.environ.setdefault("DASH_DB",
                      str(Path(tempfile.mkdtemp()) / "dash_pdf.db"))

from fastapi.testclient import TestClient  # noqa: E402

from ai_operator.graph import DEFAULT_FLOW_PATH  # noqa: E402
from platform.dashboard import db  # noqa: E402
from platform.dashboard.app import app  # noqa: E402
from platform.flow.compiler import _load_documents  # noqa: E402
from platform.dashboard.pdftext import extract_pdf_text  # noqa: E402

db.init_db()
client = TestClient(app)

TENANT_DIR = DEFAULT_FLOW_PATH.parents[1] / "company_data" / "acme"


def minimal_pdf(text: str = "Hello PDF") -> bytes:
    """A one-page PDF with a correct xref table (no library needed)."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n").encode()
    return bytes(out)


def test_extract_pdf_text_reads_content():
    assert "Hello PDF" in extract_pdf_text(minimal_pdf())


def test_extract_rejects_non_pdf():
    import pytest

    with pytest.raises(ValueError, match="not a PDF"):
        extract_pdf_text(b"plain text pretending")


def test_upload_pdf_writes_extracted_txt_sibling():
    pdf_name = "zz_phase3_pdf.pdf"
    txt_path = TENANT_DIR / "zz_phase3_pdf.txt"
    TENANT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        resp = client.post("/documents/upload", data={"tenant": "acme"}, files={
            "file": (pdf_name, io.BytesIO(minimal_pdf("Payable note 42500")),
                     "application/pdf")},
            follow_redirects=False)
        assert resp.status_code == 303
        assert "error=" not in resp.headers["location"]
        assert (TENANT_DIR / pdf_name).is_file()
        assert txt_path.is_file()
        assert "Payable note 42500" in txt_path.read_text()
    finally:
        (TENANT_DIR / pdf_name).unlink(missing_ok=True)
        txt_path.unlink(missing_ok=True)


def test_upload_broken_pdf_stores_empty_txt_with_warning():
    pdf_name = "zz_phase3_broken.pdf"
    txt_path = TENANT_DIR / "zz_phase3_broken.txt"
    TENANT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        resp = client.post("/documents/upload", data={"tenant": "acme"}, files={
            "file": (pdf_name, io.BytesIO(b"%PDF-1.4\nnot really a pdf"),
                     "application/pdf")},
            follow_redirects=False)
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]  # visible, not silent
        from urllib.parse import unquote

        assert "extraction failed" in unquote(resp.headers["location"])
        assert txt_path.is_file()
        assert txt_path.read_text() == ""
    finally:
        (TENANT_DIR / pdf_name).unlink(missing_ok=True)
        txt_path.unlink(missing_ok=True)


def test_load_documents_prefers_extracted_txt():
    TENANT_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = TENANT_DIR / "zz_phase3_read.pdf"
    txt_path = TENANT_DIR / "zz_phase3_read.txt"
    raw_rel = f"company_data/acme/{pdf_path.name}"
    pdf_path.write_bytes(minimal_pdf("raw bytes should never be read"))
    try:
        txt_path.write_text("extracted: invoice total 42500 due 2026-07-30")
        loaded = _load_documents([raw_rel])
        assert len(loaded) == 1
        assert "extracted: invoice total 42500" in loaded[0]
        assert "raw bytes" not in loaded[0]

        txt_path.unlink()
        loaded = _load_documents([raw_rel])
        assert "no extracted text" in loaded[0]
    finally:
        pdf_path.unlink(missing_ok=True)
        txt_path.unlink(missing_ok=True)


def test_uploaded_pdf_is_attached_and_readable_in_flow_config():
    """End-to-end: upload -> attach -> the compiled prompt carries the text."""
    from platform.dashboard import flowcfg

    original = DEFAULT_FLOW_PATH.read_text()
    pdf_name = "zz_phase3_attach.pdf"
    txt_path = TENANT_DIR / "zz_phase3_attach.txt"
    TENANT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        client.post("/documents/upload", data={"tenant": "acme"}, files={
            "file": (pdf_name, io.BytesIO(minimal_pdf("contract clause 7")),
                     "application/pdf")}, follow_redirects=False)
        resp = client.post("/documents/attach", data={
            "tenant": "acme", "filename": pdf_name, "agents": ["ap_agent"]},
            follow_redirects=False)
        assert resp.status_code == 303
        cfg = flowcfg.load_cfg()
        ap = next(n for n in cfg["nodes"] if n["node_id"] == "ap_agent")
        assert any(pdf_name in d for d in ap["documents"])
        sections = _load_documents(
            [d for d in ap["documents"] if pdf_name in d])
        assert "contract clause 7" in sections[0]
    finally:
        if DEFAULT_FLOW_PATH.read_text() != original:
            DEFAULT_FLOW_PATH.write_text(original)
        (TENANT_DIR / pdf_name).unlink(missing_ok=True)
        txt_path.unlink(missing_ok=True)
