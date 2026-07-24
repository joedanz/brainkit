"""The failure contract: exit 1 with a message, never a traceback — and never
a swallowed bug.

docs/reference/cli.mdx promises "every subcommand exits 0 on success and 1 on
a handled error ... with a human-readable message on stderr". `compile` and
`cycle` did not keep it: a missing master, a typo'd org.yaml, or a full disk
came out as a stack dump, which is what a cron log would have collected.
"""

from __future__ import annotations

import importlib
import pkgutil
import subprocess
from pathlib import Path

import pytest

import brain
from brain.cli import main
from brain.errors import BrainError, describe
from tests.test_cli import seed_meta


def test_missing_master_is_handled_by_every_command(tmp_path: Path, capsys):
    gone = str(tmp_path / "not-a-vault")
    for argv in (
        ["compile", "--master", gone, "--out", str(tmp_path / "o")],
        ["cycle", "--master", gone, "--out", str(tmp_path / "o")],
        ["doctor", "--master", gone],
    ):
        assert main(argv) == 1, argv
        out, err = capsys.readouterr()
        assert "Traceback" not in out + err, argv
        # Either stream satisfies the contract: doctor reports this as a
        # finding on stdout, where its --json consumers expect it, while the
        # rest go to stderr. What matters is that the message names the file,
        # because the overwhelmingly common cause is --master off by one
        # directory.
        assert "org.yaml" in out + err, argv


def test_unparseable_yaml_names_the_file(master: Path, tmp_path: Path, capsys):
    seed_meta(master)
    (master / "_meta/org.yaml").write_text("people: [unclosed\n")

    assert main(["compile", "--master", str(master), "--out", str(tmp_path / "o")]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "org.yaml does not parse" in err


def test_unwritable_output_reports_the_path(master: Path, tmp_path: Path, capsys):
    seed_meta(master)
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert main(["compile", "--master", str(master),
                     "--out", str(locked / "sub")]) == 1
        err = capsys.readouterr().err
        assert "Traceback" not in err
        assert "Permission denied" in err
        assert str(locked / "sub") in err
    finally:
        locked.chmod(0o700)


def test_compile_failure_reports_how_far_the_fleet_got(master: Path, tmp_path: Path,
                                                       capsys):
    """With N vaults the operator's first question is "what state is the fleet
    in?" — so the failure has to answer it, not just name the exception."""
    seed_meta(master)
    out = tmp_path / "compiled"
    assert main(["compile", "--master", str(master), "--out", str(out)]) == 0
    capsys.readouterr()

    # Break the second person's repo so the git step fails mid-fleet. org.yaml
    # lists alice then bob, and dicts preserve insertion order.
    subprocess.run(["rm", "-rf", str(out / "bob/.git")], check=True)
    (out / "bob/.git").write_text("not a gitfile\n")

    assert main(["compile", "--master", str(master), "--out", str(out)]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "compiling bob" in err          # which person
    assert "1 of 2 vault(s)" in err        # how far it got
    assert "invalid gitfile" in err        # git's own complaint, not "exit status 128"


def test_a_bug_still_raises(monkeypatch, tmp_path: Path):
    """The catch-all must not become a bug silencer. Only errors.HANDLED is
    caught; anything else is something we did not anticipate, and a traceback
    is the honest output for that."""
    def boom(_args):
        raise AttributeError("'NoneType' object has no attribute 'id'")

    # build_parser() runs inside main() and reads cmd_doctor off the module,
    # so patching the module attribute is what reaches the parser default.
    monkeypatch.setattr("brain.cli.cmd_doctor", boom)
    with pytest.raises(AttributeError):
        main(["doctor", "--master", str(tmp_path)])


def test_keyboard_interrupt_exits_130(monkeypatch, tmp_path: Path, capsys):
    def interrupted(_args):
        raise KeyboardInterrupt

    monkeypatch.setattr("brain.cli.cmd_doctor", interrupted)
    assert main(["doctor", "--master", str(tmp_path)]) == 130
    assert "interrupted" in capsys.readouterr().err


def test_describe_unwraps_git_and_oserror():
    git = subprocess.CalledProcessError(
        128, ["git", "-C", "/vaults/bob", "add", "-A"], stderr="fatal: not a repository\n"
    )
    text = describe(git)
    assert "git -C /vaults/bob add -A" in text
    assert "fatal: not a repository" in text
    assert "non-zero exit status" not in text  # the useless default is gone

    disk = OSError(28, "No space left on device", "/vaults/bob/Memory.md")
    assert describe(disk) == "No space left on device (/vaults/bob/Memory.md)"


def test_every_domain_error_carries_the_marker():
    """A new *Error class that forgets BrainError silently loses the contract —
    it would reach the terminal as a traceback. Catch that here rather than in
    somebody's cron log."""
    missing = []
    for mod in pkgutil.iter_modules(brain.__path__):
        module = importlib.import_module(f"brain.{mod.name}")
        for name, obj in vars(module).items():
            if (isinstance(obj, type) and issubclass(obj, BaseException)
                    and obj.__module__ == module.__name__
                    and not issubclass(obj, BrainError)):
                missing.append(f"{module.__name__}.{name}")
    assert not missing, f"exception types missing BrainError: {sorted(missing)}"
