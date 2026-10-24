"""End-to-end extraction into DocumentObject for the core formats."""
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(autouse=True)
def _env():
    import core_boot
    core_boot.CONFIG["docintel_persist"] = False
    core_boot.CONFIG["docintel_use_embeddings"] = False
    core_boot.CONFIG["docintel_ocr_enabled"] = False
    import syrix_docintel.registry as reg
    reg._reset_for_tests()
    yield


def _analyze(name, blob):
    import syrix_docintel as di
    return di.analyze_upload(name, blob)


def test_txt():
    d = _analyze("notes.txt", b"Hello world.\n\nSecond paragraph about revenue of 4.2 million.")
    assert d.kind == "text" and d.status == "ready"
    assert d.chunks and "revenue" in d.full_text()
    assert d.word_count > 0


def test_code_structure():
    src = b"import os\nimport sys\n\ndef alpha():\n    return 1\n\nclass Beta:\n    pass\n"
    d = _analyze("m.py", src)
    assert d.kind == "code" and d.metadata.get("code")
    syms = d.metadata["code"]["symbols"]
    assert "alpha" in syms and "Beta" in syms
    # The import regex must not swallow following lines.
    assert all("def" not in imp for imp in d.metadata["code"].get("imports", []))


def test_csv_stats():
    rows = "n,val\n" + "\n".join(f"{i},{i*10}" for i in range(1, 11))
    d = _analyze("data.csv", rows.encode())
    assert d.kind == "spreadsheet" and d.tables
    stats = d.statistics["sheets"]["Sheet1"]
    assert stats["val"]["max"] == 100.0 and stats["val"]["sum"] == 550.0


def test_xlsx_multi_sheet_and_formulas():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "S1"
    ws.append(["a", "b"])
    ws.append([1, 2])
    ws["C2"] = "=A2+B2"
    wb.create_sheet("S2")
    b = io.BytesIO()
    wb.save(b)
    d = _analyze("book.xlsx", b.getvalue())
    assert d.kind == "spreadsheet" and d.sheets == 2
    assert d.metadata["formulas"].get("S1!C2") == "=A2+B2"


def test_docx_headings_and_table():
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_heading("Report", level=1)
    doc.add_paragraph("Revenue was strong.")
    doc.add_heading("Risks", level=1)
    doc.add_paragraph("Supply chain exposure.")
    t = doc.add_table(rows=1, cols=1)
    t.rows[0].cells[0].text = "cell"
    b = io.BytesIO()
    doc.save(b)
    d = _analyze("r.docx", b.getvalue())
    assert d.kind == "document" and d.status == "ready"
    assert any("Risks" in c.section for c in d.chunks)
    assert d.tables


def test_pdf_text():
    pytest.importorskip("pypdfium2")
    pdf = _minimal_pdf("Revenue was 4.2 million dollars")
    d = _analyze("r.pdf", pdf)
    assert d.kind == "pdf" and d.pages == 1
    assert "4.2 million" in d.full_text()


def test_zip_tree_and_safety():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("proj/main.py", "import flask\n")
        z.writestr("proj/readme.md", "# hi")
        z.writestr("proj/evil.exe", b"MZ\x90\x00")
    d = _analyze("proj.zip", b.getvalue())
    assert d.kind == "archive"
    assert d.metadata["entry_count"] == 3
    assert "python" in d.metadata.get("languages", {})
    # Executable must be flagged/blocked, never previewed.
    assert d.metadata.get("blocked_executables")


def test_failed_doc_is_honest():
    # A corrupt archive (PK header, garbage body) must fail cleanly with an
    # error/note — never a fabricated "success".
    d = _analyze("broken.zip", b"PK\x03\x04\x00\x00garbage-not-a-real-zip")
    assert d.status in ("failed", "partial")
    assert d.error or d.notes


def test_mislabeled_docx_reads_as_text():
    # A .docx that is actually plain text is read as text (magic bytes win) —
    # graceful degradation, not a crash.
    d = _analyze("fake.docx", b"just some plain readable text, not really a docx")
    assert d.status == "ready" and d.kind == "text"


def _minimal_pdf(text: str) -> bytes:
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
    ]
    stream = b"BT /F1 24 Tf 72 700 Td (" + text.encode("latin-1") + b") Tj ET"
    objs.append(b"<</Length " + str(len(stream)).encode() + b">>\nstream\n" + stream + b"\nendstream")
    objs.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + o + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += (b"trailer\n<</Size " + str(len(objs) + 1).encode() + b"/Root 1 0 R>>\nstartxref\n"
            + str(xref_pos).encode() + b"\n%%EOF")
    return out
