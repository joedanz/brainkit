"""The agent image's document tools are baked in, pinned, and import-checked at build time.

Agents run on a network allowlist that does not include PyPI, so a library the agent used to fetch with
`uv run --with pymupdf` (≈60 runs, 2026-08-28..09-22) must be in the image. The Dockerfile has no test run,
so this reads the text: every library is pinned with `==` and one RUN imports them all, so a bad pin fails the
build instead of the agent's first PDF.

Run: uv run --extra dev python -m pytest tests/test_agents_box_doctools.py -q
"""
import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "deploy" / "agents-box" / "Dockerfile"
BAKED = {"openpyxl": "openpyxl", "xlrd": "xlrd", "python-docx": "docx", "pypdf": "pypdf",
         "pymupdf": "fitz", "pillow": "PIL", "numpy": "numpy"}


def doctools_run():
    df = DOCKERFILE.read_text()
    start = df.index("RUN uv venv /opt/doctools")
    return df[start:df.index("\n\n", start)].replace("\\\n", " ")


def test_every_document_library_is_pinned_exactly():
    run = doctools_run()
    for pkg in BAKED:
        assert re.search(rf'"{re.escape(pkg)}==\d[\w.]*"', run), f"{pkg} must be pinned with =="


def test_the_build_imports_every_library_it_installs():
    run = doctools_run()
    probe = run[run.index("/opt/doctools/bin/python -c"):]
    for module in BAKED.values():
        assert re.search(rf"\b{module}\b", probe), f"the build-time import must cover {module}"


def test_ocr_is_tesseract_and_rapidocr_stays_out():
    df = DOCKERFILE.read_text()
    assert "tesseract-ocr" in df and "rapidocr" not in df.lower().replace("rapidocr was left out", "")
