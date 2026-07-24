from brain.schemas import VaultConfig
from brain.vaultmap import (
    UNTYPED,
    EntityGroup,
    NoteFacts,
    group_entities,
    link_degree,
    rank_hubs,
    scan_note,
)


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
