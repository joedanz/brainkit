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
    r'/^merge_soul\(\)/{f=1} f && /^}$/{exit}'
)
APPLY_AWK = r'/^# --- SOUL.md: managed blocks/{on=1} on{print} on && /^fi$/{exit}'


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

    def run(data: Path, snippet: str = "", env: dict[str, str] | None = None) -> str:
        exports = [f"export {k}={v!r}" for k, v in (env or {}).items()]
        script = "\n".join([
            "set -eu",
            *exports,
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


VAULT_URL = "https://joe-embark.example.test"


def test_sources_block_is_off_without_a_public_url(shell, tmp_path):
    data = _soul(tmp_path, NAMED)
    shell(data)
    soul = (data / "SOUL.md").read_text()
    assert "hermes-brain: sources" not in soul
    assert "Sources:" not in soul


def test_sources_block_names_the_url_and_the_deep_link(shell, tmp_path):
    data = _soul(tmp_path, NAMED)
    shell(data, env={"BRAIN_VAULT_PUBLIC_URL": VAULT_URL + "/"})   # trailing slash is tolerated
    soul = (data / "SOUL.md").read_text()
    assert soul.count("hermes-brain: sources (managed") == 1
    assert f"({VAULT_URL}/#note=<rel_path>)" in soul
    assert "Omit the whole" in soul                       # no footer when no note was read
    assert soul.index("hermes-brain: scratch") < soul.index("hermes-brain: sources")


def test_sources_block_is_idempotent_and_hidden_from_the_fingerprint(shell, tmp_path):
    data = _soul(tmp_path, NAMED)
    before = _fingerprint(shell, data)
    env = {"BRAIN_VAULT_PUBLIC_URL": VAULT_URL}
    shell(data, env=env)
    once = (data / "SOUL.md").read_text()
    out = shell(data, env=env)
    assert (data / "SOUL.md").read_text() == once
    assert "applied" not in out                           # second boot is quiet
    assert _fingerprint(shell, data) == before


def test_sources_block_is_withdrawn_when_the_url_goes_away(shell, tmp_path):
    data = _soul(tmp_path, NAMED)
    shell(data, env={"BRAIN_VAULT_PUBLIC_URL": VAULT_URL})
    assert "hermes-brain: sources" in (data / "SOUL.md").read_text()
    shell(data)
    soul = (data / "SOUL.md").read_text()
    assert "hermes-brain: sources" not in soul
    assert soul.count("hermes-brain: scratch (managed") == 1
    assert "Your name is Stewie." in soul


def test_the_url_is_not_shell_expanded_into_the_prompt(shell, tmp_path):
    """The URL lands in a heredoc; a `$` in it must reach SOUL.md verbatim."""
    data = _soul(tmp_path, NAMED)
    shell(data, env={"BRAIN_VAULT_PUBLIC_URL": "https://x.test/$HOME"})
    assert "https://x.test/$HOME/#note=" in (data / "SOUL.md").read_text()


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


# --- three-way merge --------------------------------------------------------
#
# The regression these pin cost four agents their names on 2026-08-14. The
# re-sync was a two-way comparison: equal-to-what-we-shipped meant overwrite,
# anything else meant leave alone — and the sentinel then recorded the LOCAL
# hash, so a customised SOUL became its own baseline and was declared
# "unmodified" on the next image change.
#
# Two-way could not have been made safe without also freezing every shipped
# improvement, because the fleet customises EVERY agent's SOUL with a
# "Your name is <X>." line. Hence the merge.

PRISTINE_BASE = "# Identity\n\nYou are a personal assistant.\n\n## Voice\n- Concise.\n"
NAMED_LOCAL = "# Identity\n\nYour name is Stewie.\n\nYou are a personal assistant.\n\n## Voice\n- Concise.\n"
NEW_IMAGE = "# Identity\n\nYou are a personal assistant.\n\n## Voice\n- Concise.\n\n## Posture\n- Draft, don't send.\n"


def _merge(shell, tmp_path, live_text, base_text, new_text):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    live = data / "SOUL.md"
    live.write_text(live_text)
    base = tmp_path / "base.md"
    base.write_text(base_text)
    new = tmp_path / "new.md"
    new.write_text(new_text)
    out = shell(data, f'merge_soul "{live}" "{base}" "{new}"').strip().split("\n")[-1]
    return out, live.read_text()


def test_merge_keeps_the_local_name_and_takes_the_image_change(shell, tmp_path):
    """THE regression. The agent keeps its name AND gains the new section."""
    verdict, soul = _merge(shell, tmp_path, NAMED_LOCAL, PRISTINE_BASE, NEW_IMAGE)
    assert verdict == "merged", verdict
    assert "Your name is Stewie." in soul
    assert "Draft, don't send." in soul


def test_merge_fast_forwards_an_untouched_soul(shell, tmp_path):
    """No local edit — take the image's version whole, as before."""
    verdict, soul = _merge(shell, tmp_path, PRISTINE_BASE, PRISTINE_BASE, NEW_IMAGE)
    assert verdict == "fastfwd", verdict
    assert soul == NEW_IMAGE


def test_merge_is_a_noop_when_the_image_soul_did_not_move(shell, tmp_path):
    verdict, soul = _merge(shell, tmp_path, NAMED_LOCAL, PRISTINE_BASE, PRISTINE_BASE)
    assert verdict == "unchanged", verdict
    assert "Your name is Stewie." in soul


def test_a_conflict_keeps_the_local_file(shell, tmp_path):
    """Both sides rewrote the same line. Losing the local one is the failure
    this whole mechanism exists to prevent, so the image's change is dropped
    and the operator is told."""
    theirs = PRISTINE_BASE.replace("You are a personal assistant.", "You are an executive assistant.")
    mine = PRISTINE_BASE.replace("You are a personal assistant.", "You are Joe's assistant.")
    verdict, soul = _merge(shell, tmp_path, mine, PRISTINE_BASE, theirs)
    assert verdict == "conflict", verdict
    assert "You are Joe's assistant." in soul
    assert "executive assistant" not in soul
    assert "<<<<<<<" not in soul


def test_no_base_is_reported_rather_than_guessed(shell, tmp_path):
    """A legacy agent has no recorded base. Guessing either way is how the
    original bug happened, so the caller decides."""
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "SOUL.md").write_text(NAMED_LOCAL)
    new = tmp_path / "new.md"
    new.write_text(NEW_IMAGE)
    out = shell(data, f'merge_soul "{data}/SOUL.md" "{tmp_path}/missing.md" "{new}"').strip().split("\n")[-1]
    assert out == "nobase", out
    assert "Your name is Stewie." in (data / "SOUL.md").read_text()


def test_the_scratch_block_does_not_participate_in_the_merge(shell, tmp_path):
    """Our own managed block must not read as a local edit to reconcile — it is
    stripped before merging and re-applied afterwards by the boot script."""
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "SOUL.md").write_text(NAMED_LOCAL)
    shell(data)                                    # appends the scratch block
    assert "hermes-brain: scratch" in (data / "SOUL.md").read_text()

    base = tmp_path / "base.md"
    base.write_text(PRISTINE_BASE)
    new = tmp_path / "new.md"
    new.write_text(NEW_IMAGE)
    out = shell(data, f'merge_soul "{data}/SOUL.md" "{base}" "{new}"').strip().split("\n")[-1]
    soul = (data / "SOUL.md").read_text()
    assert out == "merged", out
    assert "Your name is Stewie." in soul
    assert "Draft, don't send." in soul
    assert "hermes-brain: scratch" not in soul      # re-applied by the caller


# --- install_skills: the company's skills repo outranks the image ------------
#
# Same extraction discipline as the SOUL block above: the shell is pulled from
# the script rather than restated, so a drifting script fails at extraction
# instead of testing a stale copy.

SKILLS_AWK = r'/^COMPANY_SKILLS=/{on=1} on{print} on && /^install_skills\(\)/{f=1} f && /^}$/{exit}'


@pytest.fixture(scope="module")
def skills_shell():
    """Drive install_skills against a temp image profile and DATA."""
    if shutil.which("diff") is None:
        pytest.skip("diff not available")
    body = _extract(SKILLS_AWK)

    def run(profile: Path, data: Path, company: Path | None) -> str:
        script = "\n".join([
            "set -eu",
            f'DATA="{data}"',
            f'COMPANY_SKILLS="{company or "/nonexistent"}"',
            # the script reads the image profile from an absolute path; point
            # the loop at the fixture's copy without editing the extracted body
            body.replace("/opt/brain-profile/skills", str(profile)),
            "install_skills",
        ])
        r = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout

    return run


def _skill(root: Path, name: str, text: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(text)
    return d


def test_image_skills_install_when_no_company_repo(skills_shell, tmp_path):
    profile, data = tmp_path / "profile", tmp_path / "data"
    _skill(profile, "brain-protocol", "image version\n")
    _skill(profile, "note-taking", "notes\n")
    skills_shell(profile, data, None)
    assert (data / "skills/brain-protocol/SKILL.md").read_text() == "image version\n"
    assert (data / "skills/note-taking/SKILL.md").exists()


def test_a_company_skill_is_not_seeded_from_the_image(skills_shell, tmp_path):
    """The whole point: an image copy in DATA shadows the company's, so a push
    to the skills repo would never take effect."""
    profile, data, company = tmp_path / "profile", tmp_path / "data", tmp_path / "company"
    _skill(profile, "brain-protocol", "image version\n")
    _skill(profile, "note-taking", "notes\n")
    _skill(company, "brain-protocol", "company version\n")

    out = skills_shell(profile, data, company)
    assert not (data / "skills/brain-protocol").exists()
    assert (data / "skills/note-taking/SKILL.md").exists(), "other skills still seed"
    assert "company-managed" in out


def test_a_company_skill_under_a_category_is_recognised(skills_shell, tmp_path):
    """The repo's layout allows <category>/<skill>/SKILL.md."""
    profile, data, company = tmp_path / "profile", tmp_path / "data", tmp_path / "company"
    _skill(profile, "brain-protocol", "image version\n")
    _skill(company / "ops", "brain-protocol", "company version\n")
    skills_shell(profile, data, company)
    assert not (data / "skills/brain-protocol").exists()


def test_an_unedited_seeded_copy_is_reclaimed(skills_shell, tmp_path):
    """The migration path: a copy this script wrote, byte-identical to the
    image's, is removed so the company's stops being shadowed."""
    profile, data, company = tmp_path / "profile", tmp_path / "data", tmp_path / "company"
    _skill(profile, "brain-protocol", "image version\n")
    _skill(company, "brain-protocol", "company version\n")
    _skill(data / "skills", "brain-protocol", "image version\n")  # previously seeded

    out = skills_shell(profile, data, company)
    assert not (data / "skills/brain-protocol").exists()
    assert "removed the image's copy" in out


def test_an_edited_local_skill_is_left_alone(skills_shell, tmp_path):
    """A locally-authored skill shadowing a company one is documented
    behaviour; reclaiming it would delete somebody's work."""
    profile, data, company = tmp_path / "profile", tmp_path / "data", tmp_path / "company"
    _skill(profile, "brain-protocol", "image version\n")
    _skill(company, "brain-protocol", "company version\n")
    _skill(data / "skills", "brain-protocol", "MY OWN EDITS\n")

    out = skills_shell(profile, data, company)
    assert (data / "skills/brain-protocol/SKILL.md").read_text() == "MY OWN EDITS\n"
    assert "not seeding" in out


# --- the provisioning script that hands the skill over -----------------------

INSTALLER = DEPLOY / "install-brain-skill.sh"


def test_installer_places_the_skill_and_excludes_it(tmp_path):
    """Provisioned, not committed: the pull job runs fetch + reset --hard and
    never git clean, so an untracked directory survives every pull. Excluding
    it locally keeps it out of `git status` so nobody reads it as a stray
    edit."""
    repo = tmp_path / "company-skills"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    r = subprocess.run(["sh", str(INSTALLER), str(repo)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    skill = repo / "brain-protocol/SKILL.md"
    assert skill.exists()
    assert skill.read_text() == (
        DEPLOY / "company-brain-profile/skills/brain-protocol/SKILL.md").read_text()

    status = subprocess.run(["git", "-C", str(repo), "status", "--short"],
                            capture_output=True, text=True).stdout
    assert status.strip() == "", f"should be excluded, got: {status!r}"


def test_installer_is_idempotent(tmp_path):
    """Re-run after every brainkit upgrade, so running twice must not double
    the exclude line or fail."""
    repo = tmp_path / "company-skills"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for _ in range(2):
        r = subprocess.run(["sh", str(INSTALLER), str(repo)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    exclude = (repo / ".git/info/exclude").read_text().splitlines()
    assert exclude.count("brain-protocol/") == 1


def test_installer_works_without_git(tmp_path):
    """A plain directory is a fine destination; only the exclude step needs a
    checkout, so its absence must not be an error."""
    dest = tmp_path / "skills"
    dest.mkdir()
    r = subprocess.run(["sh", str(INSTALLER), str(dest)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (dest / "brain-protocol/SKILL.md").exists()


def test_installer_refuses_a_missing_destination(tmp_path):
    """A typo'd path must fail loudly rather than silently creating a skills
    repo nothing is mounted from."""
    r = subprocess.run(["sh", str(INSTALLER), str(tmp_path / "nope")],
                       capture_output=True, text=True)
    assert r.returncode != 0
    assert "does not exist" in r.stderr


# --- installer: the anti-clobber guard ---------------------------------------
# The destination sits in a directory called `company-skills`, so it reads as
# the company's own file and sooner or later somebody edits it there. The
# installer used to be a bare `cp -R`, which meant that edit died silently.
# These pin the guard that replaced it: an edited copy is refused, not
# overwritten, and nothing is ever destroyed without a backup.

def _install(repo, *args):
    return subprocess.run(["sh", str(INSTALLER), str(repo), *args],
                          capture_output=True, text=True)


def _git_repo(tmp_path):
    repo = tmp_path / "company-skills"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def test_a_local_edit_is_refused_and_survives(tmp_path):
    repo = _git_repo(tmp_path)
    assert _install(repo).returncode == 0
    skill = repo / "brain-protocol/SKILL.md"
    skill.write_text(skill.read_text() + "\n## Embark addition\nour own rule\n")

    r = _install(repo)
    assert r.returncode == 2, r.stdout
    assert "REFUSING" in r.stderr
    assert "SKILL.md" in r.stderr
    # The whole point: the edit is still there.
    assert "## Embark addition" in skill.read_text()


def test_the_refusal_names_added_and_deleted_files_too(tmp_path):
    repo = _git_repo(tmp_path)
    assert _install(repo).returncode == 0
    (repo / "brain-protocol/EXTRA.md").write_text("local\n")

    r = _install(repo)
    assert r.returncode == 2
    assert "EXTRA.md (added)" in r.stderr
    assert (repo / "brain-protocol/EXTRA.md").exists()

    (repo / "brain-protocol/EXTRA.md").unlink()
    (repo / "brain-protocol/SKILL.md").unlink()
    r = _install(repo)
    assert r.returncode == 2
    assert "SKILL.md (deleted)" in r.stderr


def test_force_overwrites_but_keeps_a_backup(tmp_path):
    repo = _git_repo(tmp_path)
    assert _install(repo).returncode == 0
    skill = repo / "brain-protocol/SKILL.md"
    skill.write_text("MINE\n")

    r = _install(repo, "--force")
    assert r.returncode == 0, r.stderr
    assert skill.read_text() != "MINE\n"           # brainkit's copy is back
    backups = list(repo.glob(".brain-protocol.bak-*"))
    assert len(backups) == 1, backups
    assert (backups[0] / "SKILL.md").read_text() == "MINE\n"   # nothing lost


def test_a_legacy_copy_with_no_manifest_is_backed_up_not_lost(tmp_path):
    """Every already-provisioned box is in this state: a copy that predates the
    manifest, which nothing can classify as stock or edited. Back it up."""
    repo = _git_repo(tmp_path)
    (repo / "brain-protocol").mkdir()
    (repo / "brain-protocol/SKILL.md").write_text("PRE-EXISTING\n")

    r = _install(repo)
    assert r.returncode == 0, r.stderr
    assert "previous copy saved to" in r.stdout
    backups = list(repo.glob(".brain-protocol.bak-*"))
    assert (backups[0] / "SKILL.md").read_text() == "PRE-EXISTING\n"


def test_a_legacy_copy_that_is_already_current_needs_no_backup(tmp_path):
    """The common upgrade case must stay quiet — a backup per run would litter
    the checkout with identical copies."""
    repo = _git_repo(tmp_path)
    assert _install(repo).returncode == 0
    (repo / ".brain-protocol.manifest").unlink()       # simulate a legacy box

    r = _install(repo)
    assert r.returncode == 0, r.stderr
    assert list(repo.glob(".brain-protocol.bak-*")) == []


def test_reinstall_after_a_brainkit_change_is_allowed_and_silent(tmp_path):
    """An untouched copy is ours to replace: that is the whole delivery path."""
    repo = _git_repo(tmp_path)
    assert _install(repo).returncode == 0
    (repo / "brain-protocol/SKILL.md").write_text(
        (DEPLOY / "company-brain-profile/skills/brain-protocol/SKILL.md").read_text())
    subprocess.run(["sh", "-c", f"cd {repo}/brain-protocol && ls"], check=True, capture_output=True)

    r = _install(repo)
    assert r.returncode == 0, r.stderr
    assert "already current" in r.stdout
    assert list(repo.glob(".brain-protocol.bak-*")) == []


def test_the_manifest_and_backups_are_excluded_from_git_status(tmp_path):
    repo = _git_repo(tmp_path)
    assert _install(repo).returncode == 0
    (repo / "brain-protocol/SKILL.md").write_text("MINE\n")
    assert _install(repo, "--force").returncode == 0

    status = subprocess.run(["git", "-C", str(repo), "status", "--short"],
                            capture_output=True, text=True).stdout
    assert status.strip() == "", f"manifest/backup leaked into git status: {status!r}"


def test_the_manifest_records_what_was_installed(tmp_path):
    repo = _git_repo(tmp_path)
    assert _install(repo).returncode == 0
    manifest = (repo / ".brain-protocol.manifest").read_text()
    assert "SKILL.md" in manifest
    # Beside the skill, not inside it — a record the install wipes records
    # nothing.
    assert not (repo / "brain-protocol/.brain-protocol.manifest").exists()


def test_a_file_brainkit_no_longer_ships_does_not_linger(tmp_path):
    repo = _git_repo(tmp_path)
    assert _install(repo).returncode == 0
    stale = repo / "brain-protocol/OLD.md"
    stale.write_text("from an older brainkit\n")
    # Recorded as ours, so this is a reinstall and not a local edit.
    subprocess.run(["sh", "-c",
                    f"cd {repo}/brain-protocol && find . -type f ! -name '.*' | LC_ALL=C sort | "
                    f"while IFS= read -r f; do printf '%s  %s\\n' \"$(md5sum \"$f\" | cut -d' ' -f1)\" \"${{f#./}}\"; done "
                    f"> {repo}/.brain-protocol.manifest"], check=True)

    assert _install(repo).returncode == 0
    assert not stale.exists()


def test_an_unknown_option_is_rejected(tmp_path):
    repo = _git_repo(tmp_path)
    r = _install(repo, "--yolo")
    assert r.returncode == 1
    assert "unknown option" in r.stderr
