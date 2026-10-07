"""PDF text extraction for uploaded documents (pdfminer.six, pure Python)."""

from __future__ import annotations

import io


def extract_pdf_text(data: bytes) -> str:
    """Return the readable text of a PDF, or raise on a broken/odd file.

    Called once at upload time; the result is stored as a .txt sibling so
    agent document reads never touch raw PDF bytes.
    """
    from pdfminer.high_level import extract_text

    if b"%PDF" not in data[:1024]:
        raise ValueError("not a PDF file (missing %PDF header)")
    text = extract_text(io.BytesIO(data))
    return text or ""
