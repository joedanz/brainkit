"""The SOUL.md scratch block that 03-brain-first-boot maintains.

The block is appended to a file the profile re-sync also hashes to decide
whether a human has edited it, so the two mechanisms have to stay out of each
other's way. That interaction is what these tests pin:

  - appending is idempotent, so a container that boots daily does not grow a
    SOUL.md by one block per boot;
  - the sentinel fingerprint ignores the block, so our own append never reads
    as a human edit (which would freeze SOUL.md updates forever);
  - a real human edit still reads as one, so a later image roll does not
    overwrite it.

The shell is extracted from the script rather than restated here — a copy would
keep passing after the script it describes had changed.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy/agents-box"
HOOK = DEPLOY / "scripts/03-brain-first-boot"
DOCKERFILE = DEPLOY / "Dockerfile"

SCRATCH_REL = ".cache/tmp"

# Extract the two regions this suite drives. Both are anchored on lines that
# would have to be deliberately rewritten, so a drifting script fails loudly at
# extraction instead of silently testing nothing.
HELPERS_AWK = (
    r'/^SCRATCH_BEGIN=/{on=1} on{print} '
    r'/^soul_fingerprint\(\)/{f=1} f && /^}$/{exit}'
)
APPLY_AWK = r'/^# --- SOUL.md: where scratch files go/{on=1} on{print} on && /^fi$/{exit}'


def _extract(awk_program: str) -> str:
    out = subprocess.run(
        ["awk", awk_program, str(HOOK)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert out.strip(), f"extracted nothing from {HOOK.name} — the anchors moved"
    return out


@pytest.fixture(scope="module")
def shell():
    """Run the script's own helpers + apply-block section against a temp DATA."""
    if shutil.which("md5sum") is None:
        pytest.skip("md5sum not available (macOS without coreutils)")
    helpers = _extract(HELPERS_AWK)
    apply_block = _extract(APPLY_AWK)

    def run(data: Path, snippet: str = "") -> str:
        script = "\n".join([
            "set -eu",
            f'DATA="{data}"',
            f'SCRATCH="$DATA/{SCRATCH_REL}"',
            'file_md5() { md5sum "$1" 2>/dev/null | cut -d\' \' -f1; }',
            helpers,
            apply_block,
            snippet,
        ])
        r = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout

    return run


def _soul(tmp_path: Path, text: str) -> Path:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "SOUL.md").write_text(text)
    return data


PRISTINE = "# Identity\n\nYou are a personal assistant.\n"
NAMED = PRISTINE + "\nYour name is Stewie.\n"


def _fingerprint(shell, data: Path) -> str:
    """Last line only: the apply-block section logs to stdout when it changes
    the file, and that log would otherwise ride along with the hash."""
    out = shell(data, 'soul_fingerprint "$DATA/SOUL.md"').strip().splitlines()
    return out[-1] if out else ""


def test_block_is_appended_and_names_the_scratch_path(shell, tmp_path):
    data = _soul(tmp_path, PRISTINE)
    shell(data)
    soul = (data / "SOUL.md").read_text()
    assert "hermes-brain: scratch" in soul
    assert SCRATCH_REL in soul
    assert "You are a personal assistant." in soul, "original text must survive"


def test_reapplying_is_idempotent(shell, tmp_path):
    """A container reboots often; the file must not grow a block each time."""
    data = _soul(tmp_path, NAMED)
    shell(data)
    once = (data / "SOUL.md").read_text()
    for _ in range(3):
        shell(data)
    assert (data / "SOUL.md").read_text() == once
    assert once.count("hermes-brain: scratch (managed") == 1


def test_fingerprint_hides_the_block_from_the_resync(shell, tmp_path):
    """Our own append must not read as a human edit — that would freeze every
    future SOUL.md update from the image."""
    data = _soul(tmp_path, PRISTINE)
    before = _fingerprint(shell, data)
    shell(data)
    after = _fingerprint(shell, data)
    assert before and before == after


def test_a_human_edit_still_changes_the_fingerprint(shell, tmp_path):
    """The inverse, and the dangerous direction: an edit read as untouched
    would be overwritten by the image on the next roll."""
    data = _soul(tmp_path, PRISTINE)
    shell(data)
    pristine_fp = _fingerprint(shell, data)

    (data / "SOUL.md").write_text(
        (data / "SOUL.md").read_text() + "\nYour name is Stewie.\n")
    shell(data)
    assert _fingerprint(shell, data) != pristine_fp
    assert "Your name is Stewie." in (data / "SOUL.md").read_text()


def test_block_is_restored_after_the_image_overwrites_soul(shell, tmp_path):
    """The re-sync's `cp` of the template drops the block; the same boot that
    did the cp has to put it back."""
    data = _soul(tmp_path, PRISTINE)
    shell(data)
    (data / "SOUL.md").write_text("# Identity\n\nNew image soul.\n")
    shell(data)
    soul = (data / "SOUL.md").read_text()
    assert soul.count("hermes-brain: scratch (managed") == 1
    assert "New image soul." in soul


def test_missing_soul_is_left_missing(shell, tmp_path):
    """The heal step above owns that case — conjuring a file holding only a
    scratch note would give an agent no instructions at all."""
    data = tmp_path / "data"
    data.mkdir()
    shell(data)
    assert not (data / "SOUL.md").exists()


def test_dockerfile_tmpdir_matches_the_script(shell):
    """Two files name this directory: the ENV agents inherit and the mkdir that
    creates it. If they drift, TMPDIR points at a path nothing creates."""
    assert f"ENV TMPDIR=/opt/data/{SCRATCH_REL}" in DOCKERFILE.read_text()
    assert f'SCRATCH="$DATA/{SCRATCH_REL}"' in HOOK.read_text()


def test_scratch_stays_under_a_backup_excluded_directory():
    """`hermes backup` skips `.cache` by name. Scratch holds HAR captures, which
    carry auth headers and cookies, and those zips ship to R2."""
    assert SCRATCH_REL.startswith(".cache/")


# --- tmp-reaper ------------------------------------------------------------
# The reaper's behaviour is only observable inside a running container (s6
# service env, GNU find, a real volume), and it was verified there. What can
# drift silently on this side is its wiring and the two properties that make an
# unattended `rm -rf` loop safe — so those are pinned here.

REAPER = DEPLOY / "scripts/tmp-reaper-run"


def _reaper_code() -> str:
    """The reaper minus its comments. The comments name the anti-pattern this
    script exists to avoid (`find -mtime +N -delete`), so asserting against the
    raw text would fail on the prose that explains the code being correct."""
    return "\n".join(
        line for line in REAPER.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def test_reaper_is_wired_into_the_image():
    df = DOCKERFILE.read_text()
    assert "scripts/tmp-reaper-run /etc/services.d/tmp-reaper/run" in df
    assert "/etc/services.d/tmp-reaper/run" in df.split("RUN chmod 755")[1], \
        "the reaper is copied but never made executable — s6 would skip it"


def test_reaper_drops_privileges_by_absolute_path():
    """/command is on PATH only inside the s6 service environment. A bare
    `s6-setuidgid` makes the script unrunnable anywhere else, and a PATH change
    would turn the drop into a silent no-op with the rm loop still root."""
    src = REAPER.read_text()
    assert "SETUIDGID=/command/s6-setuidgid" in src
    assert "exec sleep infinity" in src, "must park, never continue as root"


def test_reaper_refuses_to_reap_outside_the_volume():
    """Everything the loop does is `rm -rf` driven by an env var."""
    assert "/opt/data/?*)" in REAPER.read_text()


def test_reaper_judges_whole_entries_not_individual_files():
    """The live-cache case: `find -mtime +N -delete` would strip the old files
    out of an actively-used cache and leave a half-gutted directory. Judging the
    newest descendant of each top-level entry is what keeps caches intact."""
    code = _reaper_code()
    assert "-newermt" in code and "-print -quit" in code
    assert "-delete" not in code
