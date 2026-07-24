from brain.vaultmap import NoteFacts, scan_note


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
