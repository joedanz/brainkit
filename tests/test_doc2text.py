"""doc2text — the agent image's one command for reading mail attachments.

The image runs it under /opt/doctools; here it runs under the test
interpreter, which is enough for the paths that need no third-party parser
(CSV/TXT, the refusals, truncation, the needs-OCR exit). The XLSX case runs
only where openpyxl is installed; the OCR case only where poppler and
tesseract are (the image has both).
"""
import pathlib
import shutil
import subprocess
import sys
import zlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "agents-box"
SCRIPT = DEPLOY / "scripts" / "doc2text"
DOCKERFILE = DEPLOY / "Dockerfile"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, timeout=60)


def test_dockerfile_installs_the_tools_the_script_calls():
    df = DOCKERFILE.read_text()
    assert "poppler-utils" in df                      # pdftotext, pdfinfo, pdftoppm
    assert "tesseract-ocr" in df and "tesseract-ocr-eng" in df
    assert "uv venv /opt/doctools" in df
    for pkg in ("openpyxl==", "xlrd==", "python-docx==", "pypdf=="):
        assert pkg in df, pkg                         # pinned, like the m365 CLI
    assert "COPY deploy/agents-box/scripts/doc2text /usr/local/bin/doc2text" in df


def test_script_runs_under_the_doctools_venv():
    assert SCRIPT.read_text().splitlines()[0] == "#!/opt/doctools/bin/python"


def test_csv_passes_through(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("vendor,amount\nKSZ,4500\n")
    r = run(f)
    assert r.returncode == 0 and "KSZ,4500" in r.stdout


def test_output_is_capped(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("x" * 500)
    r = run(f, "--max-chars", "100")
    assert r.returncode == 0
    assert "truncated at 100 characters of 500" in r.stdout


@pytest.mark.parametrize("name", ["page.html", "archive.zip", "noext"])
def test_unsupported_types_exit_2(tmp_path, name):
    f = tmp_path / name
    f.write_text("hi")
    r = run(f)
    assert r.returncode == 2 and "unsupported type" in r.stderr


def test_missing_file_exits_2(tmp_path):
    r = run(tmp_path / "nope.pdf")
    assert r.returncode == 2 and "no such file" in r.stderr


def test_xlsx_prints_every_sheet_as_tsv(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Payables"
    ws.append(["Vendor", "Amount", "Pay?"])
    ws.append(["KSZ Group", 4500, "pay"])
    wb.create_sheet("Empty")
    f = tmp_path / "p.xlsx"
    wb.save(f)
    r = run(f)
    assert r.returncode == 0
    assert "## Sheet: Payables" in r.stdout and "KSZ Group\t4500\tpay" in r.stdout
    assert "## Sheet: Empty" in r.stdout


def pdf_bytes(objects):
    """A well-formed PDF from object bodies (1-based; 1 is the catalog)."""
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def stream(dict_body, data):
    return b"<< %s /Length %d >>\nstream\n" % (dict_body, len(data)) + data + b"\nendstream"


def text_pdf(lines):
    ops = b"BT /F1 28 Tf 72 700 Td 36 TL " + b" ".join(b"(%s) '" % ln.encode() for ln in lines) + b" ET"
    return pdf_bytes([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        stream(b"", ops),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ])


def blank_pdf(pages=1):
    kids = b" ".join(b"%d 0 R" % (3 + i) for i in range(pages))
    return pdf_bytes([b"<< /Type /Catalog /Pages 2 0 R >>",
                      b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, pages)]
                     + [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>"] * pages)


def scanned_pdf(tmp_path, lines):
    """What a scanner sends: the page is one grayscale image, no text layer."""
    src = tmp_path / "typed.pdf"
    src.write_bytes(text_pdf(lines))
    subprocess.run(["pdftoppm", "-r", "150", "-gray", "-singlefile", str(src), str(tmp_path / "px")],
                   check=True)
    raw = (tmp_path / "px.pgm").read_bytes()
    magic, w, h, maxval, pixels = raw.split(maxsplit=4)
    assert magic == b"P5" and maxval == b"255"
    w, h = int(w), int(h)
    img = zlib.compress(pixels[: w * h])
    return pdf_bytes([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>",
        stream(b"", b"q 612 0 0 792 0 0 cm /Im0 Do Q"),
        stream(b"/Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray "
               b"/BitsPerComponent 8 /Filter /FlateDecode" % (w, h), img),
    ])


needs_poppler = pytest.mark.skipif(shutil.which("pdftotext") is None, reason="needs poppler")
needs_ocr = pytest.mark.skipif(any(shutil.which(t) is None for t in ("pdftoppm", "tesseract")),
                               reason="needs pdftoppm and tesseract")


@needs_poppler
def test_a_pdf_with_no_text_layer_exits_3_not_empty(tmp_path):
    # The 0.6.7 bug: a scan printed one newline and exited 0, so an agent
    # "read" a scanned permit or insurance notice and found nothing in it.
    f = tmp_path / "scan.pdf"
    f.write_bytes(blank_pdf(2))
    r = run(f, "--no-ocr")
    assert r.returncode == 3 and r.stdout == ""
    assert "needs OCR" in r.stderr and "no text layer" in r.stderr


@needs_poppler
def test_a_text_pdf_is_not_sent_to_ocr(tmp_path):
    f = tmp_path / "typed.pdf"
    f.write_bytes(text_pdf(["Invoice 1042 from KSZ Group", "Amount due 4,500.00"]))
    r = run(f, "--no-ocr")
    assert r.returncode == 0 and "Amount due 4,500.00" in r.stdout
    assert "[OCR" not in r.stdout


def test_an_image_with_ocr_off_exits_3(tmp_path):
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"\xff\xd8\xff")
    r = run(f, "--no-ocr")
    assert r.returncode == 3 and "needs OCR" in r.stderr


@needs_ocr
def test_a_scanned_pdf_is_read_by_local_ocr(tmp_path):
    f = tmp_path / "scan.pdf"
    f.write_bytes(scanned_pdf(tmp_path, ["INVOICE 1042", "KSZ GROUP", "AMOUNT DUE 4500"]))
    assert subprocess.run(["pdftotext", str(f), "-"], capture_output=True).stdout.strip() == b""
    r = run(f)
    assert r.returncode == 0, r.stderr
    first, _, body = r.stdout.partition("\n")
    assert first.startswith("[OCR: pages 1-1 of 1 are scanned images")
    assert "INVOICE 1042" in body and "4500" in body


@needs_ocr
def test_a_blank_scan_still_exits_3_after_ocr(tmp_path):
    f = tmp_path / "blank.pdf"
    f.write_bytes(blank_pdf())
    r = run(f)
    assert r.returncode == 3 and "OCR found no text" in r.stderr
