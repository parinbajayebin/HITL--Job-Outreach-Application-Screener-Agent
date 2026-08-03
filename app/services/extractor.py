"""PDF and text file extraction utilities.

Provides async helpers for extracting text from resumes uploaded as
PDF, plain-text, or Markdown files.  Uses *pdfminer.six* as the
primary PDF parser and falls back to *pypdf* on failure.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Final

logger = logging.getLogger(__name__)

# Supported text-based extensions (decoded as UTF-8)
_TEXT_EXTENSIONS: Final[frozenset[str]] = frozenset({".txt", ".md", ".markdown"})


# ── Public API ──────────────────────────────────────────────────────────


async def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract text content from a PDF byte stream.

    Strategy
    --------
    1. **pdfminer.six** (``pdfminer.high_level.extract_text``) — preferred
       because it handles complex layouts and multi-column resumes well.
    2. **pypdf** — fallback when pdfminer raises or returns empty text.

    Parameters
    ----------
    file_bytes:
        Raw bytes of the PDF file.

    Returns
    -------
    str
        Cleaned, normalised plain text extracted from the PDF.

    Raises
    ------
    ValueError
        If both parsers fail to extract any text.
    """
    text = _extract_with_pdfminer(file_bytes)
    if text:
        logger.debug("PDF extracted via pdfminer (%d chars)", len(text))
        return clean_text(text)

    logger.info("pdfminer returned empty text; falling back to pypdf")
    text = _extract_with_pypdf(file_bytes)
    if text:
        logger.debug("PDF extracted via pypdf (%d chars)", len(text))
        return clean_text(text)

    raise ValueError(
        "Failed to extract text from PDF — the file may be image-based "
        "or corrupted.  Consider uploading a text-layer PDF."
    )


async def extract_text_from_file(file_bytes: bytes, filename: str) -> str:
    """Dispatch extraction based on file extension.

    Parameters
    ----------
    file_bytes:
        Raw bytes of the uploaded file.
    filename:
        Original filename (used to determine the extraction method).

    Returns
    -------
    str
        Extracted and cleaned plain text.

    Raises
    ------
    ValueError
        If the file type is not supported.
    """
    ext = _get_extension(filename)

    if ext == ".pdf":
        return await extract_text_from_pdf(file_bytes)

    if ext in _TEXT_EXTENSIONS:
        try:
            return clean_text(file_bytes.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"Could not decode {filename} as UTF-8 text."
            ) from exc

    supported = ", ".join(sorted({".pdf"} | _TEXT_EXTENSIONS))
    raise ValueError(
        f"Unsupported file format '{ext}'. "
        f"Supported formats: {supported}"
    )


def clean_text(text: str) -> str:
    """Normalise whitespace, remove null bytes, and strip a text string.

    Parameters
    ----------
    text:
        Raw extracted text.

    Returns
    -------
    str
        Cleaned text with consistent whitespace.
    """
    # Remove null bytes that sometimes appear in PDF extraction
    text = text.replace("\x00", "")

    # Normalise various Unicode whitespace to plain spaces
    text = re.sub(r"[^\S\n]+", " ", text)

    # Collapse 3+ consecutive newlines into 2
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Strip leading/trailing whitespace from each line
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(lines)

    return text.strip()


# ── Private helpers ─────────────────────────────────────────────────────


def _extract_with_pdfminer(file_bytes: bytes) -> str:
    """Extract text using pdfminer.six.

    Parameters
    ----------
    file_bytes:
        Raw PDF bytes.

    Returns
    -------
    str
        Extracted text, or empty string on failure.
    """
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract

        return pdfminer_extract(io.BytesIO(file_bytes)) or ""
    except Exception as exc:
        logger.warning("pdfminer extraction failed: %s", exc)
        return ""


def _extract_with_pypdf(file_bytes: bytes) -> str:
    """Extract text using pypdf as a fallback.

    Parameters
    ----------
    file_bytes:
        Raw PDF bytes.

    Returns
    -------
    str
        Extracted text, or empty string on failure.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(file_bytes))
        pages: list[str] = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                pages.append(page_text)
        return "\n".join(pages)
    except Exception as exc:
        logger.warning("pypdf extraction failed: %s", exc)
        return ""


def _get_extension(filename: str) -> str:
    """Return the lower-cased file extension (including the dot).

    Parameters
    ----------
    filename:
        Original filename.

    Returns
    -------
    str
        Lower-cased extension, e.g. ``".pdf"``.
    """
    import os

    _, ext = os.path.splitext(filename)
    return ext.lower()
