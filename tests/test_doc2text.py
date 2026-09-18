"""doc2text — the agent image's one command for reading mail attachments.

The image runs it under /opt/doctools; here it runs under the test
interpreter, which is enough for the paths that need no third-party parser
(CSV/TXT, the refusals, truncation). The XLSX case runs only where openpyxl
is installed.
"""
import pathlib
import subprocess
import sys

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
    assert "poppler-utils" in df                      # pdftotext
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
