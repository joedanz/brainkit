from brain.vaultmap import NoteFacts, link_degree, rank_hubs, scan_note


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
