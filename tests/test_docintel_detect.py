"""Type detection: magic bytes win over the extension."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from syrix_docintel import detect, types as T


def test_pdf_by_magic_even_with_wrong_ext():
    r = detect.detect("report.txt", b"%PDF-1.4\n...")
    assert r.kind == T.KIND_PDF and r.subtype == "pdf"


def test_png_by_magic():
    r = detect.detect("x", b"\x89PNG\r\n\x1a\n\x00\x00")
    assert r.kind == T.KIND_IMAGE and r.subtype == "png"


def test_jpeg_by_magic():
    r = detect.detect("photo", b"\xff\xd8\xff\xe0abcd")
    assert r.kind == T.KIND_IMAGE and r.subtype == "jpeg"


def test_zip_plain():
    # A bare zip local-file-header with no office markers.
    r = detect.detect("bundle.zip", b"PK\x03\x04plainzip")
    assert r.kind == T.KIND_ARCHIVE and r.subtype == "zip"


def test_docx_container_detected():
    import io, zipfile
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("[Content_Types].xml", "<x/>")
        z.writestr("word/document.xml", "<x/>")
    r = detect.detect("essay.docx", b.getvalue())
    assert r.kind == T.KIND_DOCUMENT and r.subtype == "docx"


def test_xlsx_container_detected():
    import io, zipfile
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("xl/workbook.xml", "<x/>")
    r = detect.detect("sheet.xlsx", b.getvalue())
    assert r.kind == T.KIND_SPREADSHEET and r.subtype == "xlsx"


def test_csv_by_extension():
    r = detect.detect("data.csv", b"a,b,c\n1,2,3\n")
    assert r.kind == T.KIND_SPREADSHEET and r.subtype == "csv"


def test_code_by_extension():
    r = detect.detect("main.py", b"import os\n")
    assert r.kind == T.KIND_CODE and r.subtype == "python"


def test_markdown_is_text_not_code():
    r = detect.detect("README.md", b"# Title\n")
    assert r.kind == T.KIND_TEXT


def test_7z_and_rar_signatures():
    assert detect.detect("a.7z", b"7z\xbc\xaf\x27\x1c").subtype == "7z"
    assert detect.detect("a.rar", b"Rar!\x1a\x07\x00").subtype == "rar"


def test_unknown_binary():
    r = detect.detect("blob.bin", b"\x00\x01\x02\x03\x00\xff")
    assert r.kind == T.KIND_UNKNOWN


def test_plain_text_fallback_no_ext():
    r = detect.detect("noext", b"just some readable ascii text here")
    assert r.kind == T.KIND_TEXT
