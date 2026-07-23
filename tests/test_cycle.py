import json
from pathlib import Path

from brain.cli import main
from brain.cycle import run_cycle

from .test_cli import seed_meta  # ORG/SPACES yaml + git init helper
from tests.conftest import requires_vectors


def _first_compile(master: Path, tmp_path: Path) -> Path:
    out = tmp_path / "compiled"
    main(["compile", "--master", str(master), "--out", str(out)])
    return out


def _add_carol_to_org(master: Path) -> None:
    """Add carol (non-admin) to org.yaml for testing non-owner share requests."""
    org_yaml = (master / "_meta/org.yaml").read_text()
    org_yaml = org_yaml.replace(
        "bob:   {name: Bob Rivera, teams: [ops], email: bob@acme.com}",
        "bob:   {name: Bob Rivera, teams: [ops], email: bob@acme.com}\n  carol: {name: Carol, teams: [], email: carol@acme.com}"
    )
    (master / "_meta/org.yaml").write_text(org_yaml)


def test_cycle_applies_writebacks_sweeps_and_recompiles(master, tmp_path):
    seed_meta(master)
    out = _first_compile(master, tmp_path)

    # bob edits his own space (valid) and drafts a promotion
    (out / "bob/People/bob/Memory.md").write_text("Bob learned a thing.\n")
    promo = out / "bob/People/bob/Promotions/share-sop.md"
    promo.parent.mkdir(parents=True, exist_ok=True)
    promo.write_text(
        "---\ntarget-path: Company/Playbook/SOP.md\n"
        "source: People/bob/Memory.md\n---\nThe SOP body.\n"
    )
    # promotion draft must reach master before sweep can see it
    report = run_cycle(master, out, today="2026-07-07")

    assert report.ok
    bob = next(w for w in report.writebacks if w.person_id == "bob")
    assert bob.status == "applied" and bob.applied == 2
    assert (master / "People/bob/Memory.md").read_text() == "Bob learned a thing.\n"
    assert report.swept == 1
    assert (master / "_meta/promotions/pending/bob-share-sop.md").exists()
    assert report.pending == 1
    assert report.compiled == 2  # alice + bob recompiled
    # recompile refreshed bob's vault from master (draft was swept out)
    assert not (out / "bob/People/bob/Promotions/share-sop.md").exists()


def test_cycle_materializes_client_and_isolates_it(master, tmp_path):
    from brain.clients import request_client
    from brain.resolver import can_read, can_write_path
    from brain.schemas import Person, load_spaces

    seed_meta(master)
    out = _first_compile(master, tmp_path)

    # bob's agent requests a client from his own slice (as write-back would land it)
    request_client(out / "bob", "bob", "Danziger Family",
                   "Mikey (football), Roslyn (basketball).\n", "2026-07-22")

    report = run_cycle(master, out, today="2026-07-22")
    assert report.ok
    assert report.clients_created == 1

    # space + owner-bound grant now in master
    rules = load_spaces(master / "_meta/spaces.yaml")
    bob = Person(id="bob", name="Bob Rivera", teams=("ops",))
    assert can_write_path("Clients/Danziger Family/x.md", bob, rules)
    assert (master / "Clients/Danziger Family/Danziger Family.md").exists()

    # appears WRITABLE in bob's recompiled slice this same cycle (rules reloaded)
    assert (out / "bob/Clients/Danziger Family/Danziger Family.md").exists()
    # alice is a seeded org admin (roles: [admin]); append_client_grant always
    # includes "role:admin" in the owner-bound grant's subjects (oversight, by
    # design — see brain/clients.py), and the exact "Clients/Danziger Family"
    # rule wins outright over the "Clients/*" wildcard in the resolver. So
    # alice legitimately sees it too — that isn't a fail-closed violation.
    assert (out / "alice/Clients/Danziger Family/Danziger Family.md").exists()
    # fail-closed isolation: a bystander with no ownership and no admin role
    # gets neither read nor write, regardless of the Clients/* wildcard.
    outsider = Person(id="carol", name="Carol", roles=(), teams=())
    assert not can_read("Clients/Danziger Family", outsider, rules)


def test_cycle_rejection_isolated_and_reported(master, tmp_path):
    seed_meta(master)
    out = _first_compile(master, tmp_path)

    (out / "bob/Company/Home.md").write_text("defaced\n")          # out of scope
    (out / "alice/People/alice/Memory.md").write_text("ok edit\n")  # valid

    report = run_cycle(master, out, today="2026-07-07")

    assert not report.ok
    bob = next(w for w in report.writebacks if w.person_id == "bob")
    alice = next(w for w in report.writebacks if w.person_id == "alice")
    assert bob.status == "rejected" and bob.violations
    assert alice.status == "applied" and alice.applied == 1
    # master never took the defaced file; alice's edit landed
    assert (master / "Company/Home.md").read_text() != "defaced\n"
    assert (master / "People/alice/Memory.md").read_text() == "ok edit\n"
    # compile still ran for everyone: bob's vault was refreshed from master
    assert (out / "bob/Company/Home.md").read_text() != "defaced\n"


def test_cycle_skips_vault_without_manifest(master, tmp_path):
    seed_meta(master)
    out = tmp_path / "compiled"          # never compiled: no vaults yet
    report = run_cycle(master, out, today="2026-07-07")
    assert report.ok
    assert all(w.status == "skipped" for w in report.writebacks)
    assert report.compiled == 2          # first compile creates the vaults
    assert (out / "bob/People/bob/Memory.md").exists()


def test_cli_cycle_json_and_exit_codes(master, tmp_path, capsys):
    seed_meta(master)
    out = _first_compile(master, tmp_path)
    capsys.readouterr()  # drop compile output

    (out / "alice/People/alice/Memory.md").write_text("note\n")
    code = main(["cycle", "--master", str(master), "--out", str(out), "--json"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert {w["person_id"]: w["status"] for w in report["writebacks"]} == {
        "alice": "applied", "bob": "applied",
    }

    (out / "bob/Company/Home.md").write_text("defaced\n")
    code = main(["cycle", "--master", str(master), "--out", str(out), "--json"])
    assert code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False


def test_cli_cycle_human_output(master, tmp_path, capsys):
    seed_meta(master)
    out = _first_compile(master, tmp_path)
    capsys.readouterr()
    code = main(["cycle", "--master", str(master), "--out", str(out)])
    assert code == 0
    text = capsys.readouterr().out
    assert "swept" in text and "compiled" in text


def test_cycle_skips_corrupt_manifest_and_isolates_others(master, tmp_path, capsys):
    """A present-but-wrong-shape manifest must not abort the whole cycle: that
    person is skipped (with a reason), everyone else is still processed, and the
    recompile heals the manifest so the next cycle self-recovers.
    """
    seed_meta(master)
    out = _first_compile(master, tmp_path)

    (out / "alice/People/alice/Memory.md").write_text("alice valid edit\n")
    (out / "bob/.brain-manifest.json").write_text("{}")  # valid JSON, wrong shape

    capsys.readouterr()  # drop buffered compile output
    code = main(["cycle", "--master", str(master), "--out", str(out), "--json"])
    report = json.loads(capsys.readouterr().out)

    statuses = {w["person_id"]: w for w in report["writebacks"]}
    assert statuses["bob"]["status"] == "skipped"
    assert statuses["bob"]["violations"]  # reason surfaced, not a crash
    assert statuses["alice"]["status"] == "applied"  # not blocked by bob
    assert "alice valid edit" in (master / "People/alice/Memory.md").read_text()
    assert code == 0  # a skip does not flip ok; doctor is the error gate
    # recompile rewrote bob's manifest -> next cycle sees a clean baseline
    import json as _json
    healed = _json.loads((out / "bob/.brain-manifest.json").read_text())
    assert "compiled" in healed and "generated" in healed


def test_cli_writeback_corrupt_manifest_is_clean_error(master, tmp_path, capsys):
    seed_meta(master)
    out = _first_compile(master, tmp_path)
    (out / "bob/.brain-manifest.json").write_text("{ not json")

    code = main(["writeback", "--master", str(master),
                 "--vault", str(out / "bob"), "--person", "bob"])
    assert code == 1
    err = capsys.readouterr().err
    assert "cannot write back" in err and "manifest" in err
    assert "Traceback" not in err  # handled, not a crash


# ---- retrieval integration (brain cycle --index) ------------------------- #

class _CountingFake:
    """Module-level spy so counts persist across the fresh provider_from_config
    call each cycle makes."""
    model = "fake-32"
    dim = 32

    def __init__(self):
        self.embed_texts = 0

    def embed(self, texts):
        from brain.embeddings import FakeEmbeddingProvider
        self.embed_texts += len(texts)
        return FakeEmbeddingProvider().embed(texts)


@requires_vectors
def test_cycle_index_builds_per_person_indexes(master, tmp_path, monkeypatch):
    seed_meta(master)
    out = _first_compile(master, tmp_path)
    spy = _CountingFake()
    monkeypatch.setattr("brain.embeddings.provider_from_config", lambda: spy)

    report = run_cycle(master, out, today="2026-07-07", index=True)
    assert report.ok
    assert report.indexed == 2  # alice + bob
    assert (out / "alice/.brain/index.db").is_file()
    assert (out / "bob/.brain/index.db").is_file()
    assert spy.embed_texts > 0


@requires_vectors
def test_cycle_index_reuses_cache_on_second_run(master, tmp_path, monkeypatch):
    seed_meta(master)
    out = _first_compile(master, tmp_path)
    spy = _CountingFake()
    monkeypatch.setattr("brain.embeddings.provider_from_config", lambda: spy)

    run_cycle(master, out, today="2026-07-07", index=True)
    after_first = spy.embed_texts
    assert after_first > 0
    # nothing changed in master → the second cycle re-embeds nothing
    run_cycle(master, out, today="2026-07-08", index=True)
    assert spy.embed_texts == after_first


def test_cycle_without_index_flag_builds_no_index(master, tmp_path):
    seed_meta(master)
    out = _first_compile(master, tmp_path)
    run_cycle(master, out, today="2026-07-07")
    assert not (out / "alice/.brain").exists()
    assert not (out / "bob/.brain").exists()


# ---- Shares.md lifecycle -------------------------------------------------- #

def test_cycle_report_owner_mismatch_flips_ok():
    from brain.cycle import CycleReport
    tamper = CycleReport(writebacks=[], swept=0, compiled=0, pending=0,
                         clients_rejected=1, clients_tampering=1)
    assert tamper.ok is False
    routine = CycleReport(writebacks=[], swept=0, compiled=0, pending=0,
                          clients_rejected=1, clients_tampering=0)
    assert routine.ok is True  # a "name taken" rejection alone must not trip ok


def test_cycle_owner_mismatch_request_trips_ok(master, tmp_path):
    from brain.clients import request_client
    seed_meta(master)
    out = _first_compile(master, tmp_path)
    rel = request_client(out / "bob", "bob", "Danziger", "body\n", "2026-07-22")
    p = out / "bob" / rel
    p.write_text(p.read_text().replace("owner: bob", "owner: alice"))  # tamper
    report = run_cycle(master, out, today="2026-07-22")
    assert report.clients_tampering == 1
    assert report.ok is False


def test_cycle_report_shares_tampering_flips_ok():
    from brain.cycle import CycleReport
    tamper = CycleReport(writebacks=[], swept=0, compiled=0, pending=0,
                         shares_tampering=1)
    assert tamper.ok is False
    routine = CycleReport(writebacks=[], swept=0, compiled=0, pending=0,
                          shares_queued=2, shares_revoked=1)
    assert routine.ok is True  # routine rejections don't flip ok


def test_cycle_share_request_lifecycle(master, tmp_path):
    from brain.clients import request_client
    from brain.shares import list_pending_shares, request_share

    seed_meta(master)
    _add_carol_to_org(master)

    out = _first_compile(master, tmp_path)
    # cycle 1: bob creates a client (auto)
    request_client(out / "bob", "bob", "Danziger Family", "fam\n", "2026-07-22")
    run_cycle(master, out, today="2026-07-22")
    # cycle 2: bob asks to share it with carol (not yet approved)
    request_share(out / "bob", "bob", "Clients/Danziger Family",
                  "person:carol", "read", "2026-07-23")
    report = run_cycle(master, out, today="2026-07-23")
    assert report.ok and report.shares_queued == 1
    assert len(list_pending_shares(master)) == 1
    # not yet approved: carol's slice from the same cycle lacks the space
    assert not (out / "carol/Clients/Danziger Family").exists()


def test_cycle_nonowner_share_request_trips_ok(master, tmp_path):
    from brain.clients import request_client
    from brain.shares import request_share

    seed_meta(master)
    _add_carol_to_org(master)

    out = _first_compile(master, tmp_path)
    request_client(out / "bob", "bob", "Danziger Family", "fam\n", "2026-07-22")
    run_cycle(master, out, today="2026-07-22")
    # carol (not the owner of Clients/Danziger Family) requests a share on it
    # This is tampering because carol cannot write to this space
    request_share(out / "carol", "carol", "Clients/Danziger Family",
                  "person:alice", "write", "2026-07-23")
    report = run_cycle(master, out, today="2026-07-23")
    assert report.shares_tampering == 1
    assert report.ok is False


def test_shares_note_tracks_promotion_lifecycle(master, tmp_path):
    from brain.promotions import approve, draft_into_space

    seed_meta(master)

    # cycle 0: baseline compile so bob has a slice
    out = _first_compile(master, tmp_path)

    # bob's agent drafts a promotion in his own space (as write-back would land it)
    draft_into_space(master, "bob", "Company/Playbook/S.md",
                     "People/bob/Sessions/call.md", "shareable\n", "2026-07-18")

    # cycle 1: sweep queues it; his slice's Shares.md shows it pending
    run_cycle(master, out, today="2026-07-18")
    note = out / "bob/People/bob/Shares.md"
    assert "Awaiting approval" in note.read_text()

    # tampering with the generated note neither writes back nor survives
    note.write_text("forged status\n")
    approve(master, "bob-2026-07-18-s", approver="alice", date="2026-07-19")
    report = run_cycle(master, out, today="2026-07-19")
    assert report.ok  # write-back reported no rejected changes
    text = note.read_text()
    assert "forged" not in text
    assert "✅ `Company/Playbook/S.md` — approved 2026-07-19 by alice" in text
    # A generated file must never become a real note in master — if it were
    # miscategorized as `compiled`, write-back would apply the forged edit here.
    assert not (master / "People/bob/Shares.md").exists()
    # And the tampered generated file must not even register as a writeback change.
    bob_wb = next(w for w in report.writebacks if w.person_id == "bob")
    assert bob_wb.applied == 0
