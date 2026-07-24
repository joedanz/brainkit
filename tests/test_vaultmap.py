from pathlib import Path

from brain.schemas import VaultConfig
from brain.vaultmap import (
    EXEMPLARS,
    HUB_CAP,
    MAP_LIMIT,
    SPACE_CAP,
    TYPE_CAP,
    UNTYPED,
    EntityGroup,
    NoteFacts,
    Pending,
    collect_pending,
    group_entities,
    link_degree,
    rank_hubs,
    render_map,
    scan_note,
)
from tests.conftest import BOB


def test_scan_note_plain_body():
    facts = scan_note("# Home\nSee [[Big Deal]] and [[Q3 Pipeline]].\n")
    assert facts == NoteFacts(entity="", targets=("Big Deal", "Q3 Pipeline"))


def test_scan_note_reads_entity_type_from_frontmatter():
    text = "---\nentity: client\naliases: [Acme Corp, ACME]\n---\n# Acme\n"
    assert scan_note(text).entity == "client"


def test_scan_note_counts_frontmatter_wikilinks():
    # Typed relations live in frontmatter and ARE wikilinks — the indexer
    # scans the whole file, so the map must too or degrees disagree.
    text = "---\nup: [[Parent Note]]\n---\nBody links [[Other]].\n"
    assert scan_note(text).targets == ("Parent Note", "Other")


def test_scan_note_strips_alias_and_heading():
    facts = scan_note("[[Real Target#Section|display text]]\n")
    assert facts.targets == ("Real Target",)


def test_scan_note_untyped_page_has_empty_entity():
    assert scan_note("---\ntitle: Notes\n---\nbody\n").entity == ""


def test_link_degree_counts_both_ends():
    notes = {
        "Company/Home.md": NoteFacts("", ("Runbook",)),
        "Teams/ops/Runbook.md": NoteFacts("", ()),
    }
    assert link_degree(notes) == {
        "Company/Home.md": 1,
        "Teams/ops/Runbook.md": 1,
    }


def test_link_degree_resolves_by_lowercased_stem():
    notes = {
        "Company/Home.md": NoteFacts("", ("teams/ops/RUNBOOK.md",)),
        "Teams/ops/Runbook.md": NoteFacts("", ()),
    }
    assert link_degree(notes)["Teams/ops/Runbook.md"] == 1


def test_link_degree_ignores_unresolvable_targets():
    notes = {"Company/Home.md": NoteFacts("", ("Nowhere", "Also Nowhere"))}
    assert link_degree(notes) == {"Company/Home.md": 0}


def test_link_degree_ignores_self_links():
    notes = {"Company/Home.md": NoteFacts("", ("Home",))}
    assert link_degree(notes) == {"Company/Home.md": 0}


def test_rank_hubs_orders_by_degree_then_path():
    degree = {"a.md": 1, "b.md": 5, "c.md": 1, "d.md": 0}
    assert rank_hubs(degree, cap=10) == [("b.md", 5), ("a.md", 1), ("c.md", 1)]


def test_rank_hubs_applies_cap():
    degree = {f"{i}.md": 10 - i for i in range(10)}
    assert len(rank_hubs(degree, cap=3)) == 3


def test_group_entities_buckets_by_frontmatter_type():
    notes = {
        "Clients/Acme/Acme.md": NoteFacts("client", ()),
        "Clients/Globex/Globex.md": NoteFacts("client", ()),
        "Clients/Sarah Kim/Sarah Kim.md": NoteFacts("person", ()),
        "People/bob/Memory.md": NoteFacts("", ()),
    }
    spaces_rw = [
        ("Clients/Acme", True), ("Clients/Globex", True),
        ("Clients/Sarah Kim", True), ("People/bob", True),
    ]
    groups = group_entities(notes, spaces_rw, link_degree(notes), VaultConfig())
    assert [(g.etype, g.count) for g in groups] == [("client", 2), ("person", 1)]


def test_group_entities_exemplars_ordered_by_degree():
    notes = {
        "Clients/Acme/Acme.md": NoteFacts("client", ()),
        "Clients/Globex/Globex.md": NoteFacts("client", ("Acme",)),
        "Clients/Initech/Initech.md": NoteFacts("client", ("Acme",)),
    }
    spaces_rw = [("Clients/Acme", True), ("Clients/Globex", True),
                 ("Clients/Initech", True)]
    groups = group_entities(notes, spaces_rw, link_degree(notes), VaultConfig())
    # Acme has degree 2 (linked from both), the others 1 each.
    assert groups[0].exemplars[0] == "Acme"


def test_group_entities_respects_exemplar_cap():
    notes = {f"Clients/C{i}/C{i}.md": NoteFacts("client", ()) for i in range(10)}
    spaces_rw = [(f"Clients/C{i}", True) for i in range(10)]
    groups = group_entities(notes, spaces_rw, link_degree(notes), VaultConfig(),
                            exemplars=3)
    assert groups[0].count == 10
    assert len(groups[0].exemplars) == 3


def test_group_entities_space_with_no_typed_note_is_untyped():
    # A provisioned-but-unwritten entity space still exists; dropping it would
    # be the worst failure for an orientation file.
    notes = {"Clients/Empty/README.md": NoteFacts("", ())}
    groups = group_entities(notes, [("Clients/Empty", True)],
                            link_degree(notes), VaultConfig())
    assert groups == [EntityGroup(etype=UNTYPED, count=1, exemplars=("Empty",))]


def test_group_entities_honors_configured_entity_tree():
    notes = {"Families/Rivera/Rivera.md": NoteFacts("family", ())}
    config = VaultConfig(entities="Families", entity="family")
    groups = group_entities(notes, [("Families/Rivera", True)],
                            link_degree(notes), config)
    assert [(g.etype, g.count) for g in groups] == [("family", 1)]


def test_group_entities_ignores_non_entity_spaces():
    notes = {"People/bob/Memory.md": NoteFacts("", ())}
    assert group_entities(notes, [("People/bob", True)],
                          link_degree(notes), VaultConfig()) == []


def test_collect_pending_counts_inbox_and_routing(tmp_path: Path):
    base = tmp_path / "People/bob"
    (base / "Inbox").mkdir(parents=True)
    (base / "Inbox/a.md").write_text("one\n")
    (base / "Inbox/b.md").write_text("two\n")
    (base / "Needs-Routing.md").write_text(
        "---\ntitle: Needs Routing\n---\n- thing one\n\n- thing two\n")
    assert collect_pending(tmp_path, BOB) == Pending(inbox=2, needs_routing=2)


def test_collect_pending_absent_files(tmp_path: Path):
    assert collect_pending(tmp_path, BOB) == Pending(inbox=0, needs_routing=None)


def test_collect_pending_counts_nested_inbox_files(tmp_path: Path):
    nested = tmp_path / "People/bob/Inbox/sub"
    nested.mkdir(parents=True)
    (nested / "c.md").write_text("three\n")
    assert collect_pending(tmp_path, BOB).inbox == 1


def test_collect_pending_survives_undecodable_needs_routing(tmp_path: Path):
    base = tmp_path / "People/bob"
    base.mkdir(parents=True)
    (base / "Needs-Routing.md").write_bytes(b"\xff\xfe not utf-8\n")
    # Must not raise — a compile can never fail on a note's bytes.
    assert collect_pending(tmp_path, BOB).needs_routing == 1


_CFG = VaultConfig()


def _render(*, spaces_rw=None, groups=(), hubs=(), pending=None, notes=3,
            space_notes=None, person=BOB):
    return render_map(
        person,
        spaces_rw if spaces_rw is not None else [("Company", False),
                                                 ("People/bob", True)],
        notes,
        space_notes if space_notes is not None else {"Company": 2,
                                                     "People/bob": 1},
        list(groups),
        list(hubs),
        pending or Pending(inbox=0, needs_routing=None),
        _CFG,
    )


def test_render_map_has_generated_frontmatter_and_agents_pointer():
    text = _render()
    assert text.startswith("---\ngenerated: true\n---\n")
    assert "AGENTS.md" in text
    assert "brain_search" in text  # tells the agent where lookups go


def test_render_map_spaces_table_shows_counts_and_access():
    text = _render()
    assert "| `Company` | 2 | read-only |" in text
    assert "| `People/bob` | 1 | writable |" in text


def test_render_map_omits_entity_spaces_from_the_spaces_table():
    text = _render(spaces_rw=[("People/bob", True), ("Clients/Acme", True)],
                   space_notes={"People/bob": 1, "Clients/Acme": 1})
    assert "Clients/Acme" not in text.split("## Entities")[0]


def test_render_map_entity_line_carries_count_and_exemplars():
    groups = [EntityGroup("client", 187, ("Acme", "Globex", "Initech"))]
    text = _render(groups=groups)
    assert "**client** (187)" in text
    assert "[[Acme]], [[Globex]], [[Initech]]" in text


def test_render_map_degrades_at_type_level_not_name_level():
    groups = [EntityGroup(f"type{i}", 10 - (i % 3), ("A", "B", "C"))
              for i in range(TYPE_CAP + 3)]
    text = _render(groups=groups)
    assert "…and 3 more types" in text
    assert "more entities" not in text  # names are never partially hidden


def test_render_map_caps_the_spaces_table():
    spaces = [(f"Teams/t{i:02d}", True) for i in range(SPACE_CAP + 5)]
    text = _render(spaces_rw=spaces, space_notes={s: 1 for s, _ in spaces})
    assert "…and 5 more spaces" in text


def test_render_map_hubs_render_as_wikilinks_with_degree():
    text = _render(hubs=[("Company/Home.md", 24)])
    assert "[[Home]]" in text
    assert "24 link(s)" in text
    assert "`Company/Home.md`" in text


def test_render_map_omits_empty_sections():
    text = _render(groups=(), hubs=())
    assert "## Entities" not in text
    assert "## Hubs" not in text
    assert "## Pending" in text  # always present


def test_render_map_pending_omits_absent_needs_routing():
    text = _render(pending=Pending(inbox=3, needs_routing=None))
    assert "Inbox: 3 item(s)" in text
    assert "Needs-Routing" not in text
    assert "People/bob/Shares.md" in text


def test_render_map_pending_shows_needs_routing_when_present():
    text = _render(pending=Pending(inbox=0, needs_routing=4))
    assert "Needs-Routing.md`: 4 line(s)" in text


def test_render_map_size_is_independent_of_entity_count():
    small = [EntityGroup("client", 25, ("Acme", "Globex", "Initech"))]
    huge = [EntityGroup("client", 5000, ("Acme", "Globex", "Initech"))]
    # Only the printed numerals differ: the header total and the group count,
    # "25" -> "5000" twice = 4 chars. Verified against the reference render.
    assert len(_render(groups=huge)) - len(_render(groups=small)) == 4


def test_render_map_adversarial_input_stays_within_budget():
    """The test that would have caught the original enumerate-everything
    design. Every field at or past its truncation length, every list past
    its cap."""
    from brain.schemas import Person

    long = "X" * 200
    person = Person(id="p" * 64, name=long, roles=(), teams=())
    spaces = [(f"Teams/{long}{i}", i % 2 == 0) for i in range(SPACE_CAP + 40)]
    groups = [
        EntityGroup(f"{long}{i}", 5000, tuple(f"{long}{j}" for j in range(EXEMPLARS)))
        for i in range(TYPE_CAP + 18)
    ]
    hubs = [(f"Company/{long}{i}.md", 9999) for i in range(HUB_CAP)]
    text = render_map(
        person, spaces, 99999, {s: 99999 for s, _ in spaces}, groups, hubs,
        Pending(inbox=999, needs_routing=999), _CFG,
    )
    assert len(text) <= MAP_LIMIT, f"{len(text)} chars exceeds {MAP_LIMIT}"
    # No single rendered field escaped truncation.
    assert long not in text


def test_render_map_realistic_vault_is_small():
    groups = [EntityGroup("client", 187, ("Acme Corporation", "Globex", "Initech"))]
    hubs = [(f"Company/Playbook/Note {i}.md", 30 - i) for i in range(HUB_CAP)]
    text = _render(groups=groups, hubs=hubs,
                   pending=Pending(inbox=3, needs_routing=4))
    assert len(text) < MAP_LIMIT // 2


def test_render_map_never_raises_on_empty_vault():
    text = render_map(BOB, [], 0, {}, [], [], Pending(0, None), _CFG)
    assert "## Pending" in text
