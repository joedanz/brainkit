from brain.chunker import Chunk, chunk_markdown, embedding_input


def test_heading_path_nesting():
    text = (
        "# A\n" + "a" * 300 + "\n"
        "## B\n" + "b" * 300 + "\n"
        "### C\n" + "c" * 300 + "\n"
    )
    # small target so sibling sections don't merge — exercises exact heading paths
    chunks = chunk_markdown("Company/Doc.md", text, target_chars=200)
    hps = [c.heading_path for c in chunks]
    assert "A" in hps
    assert "A > B" in hps
    assert "A > B > C" in hps


def test_frontmatter_stripped():
    text = "---\npromotion-id: SECRETTOKEN\n---\n# Body\nreal content\n"
    chunks = chunk_markdown("Company/Doc.md", text)
    assert all("SECRETTOKEN" not in c.text for c in chunks)
    assert any("real content" in c.text for c in chunks)


def test_big_section_splits_on_paragraphs_within_bounds():
    para = "Sentinel paragraph number {}. " + "word " * 40
    body = "# Big\n" + "\n\n".join(para.format(i) for i in range(20))
    chunks = chunk_markdown("Company/Big.md", body, target_chars=800, max_chars=1200)
    assert len(chunks) > 1
    assert all(len(c.text) <= 1200 for c in chunks)
    # a whole paragraph is never broken across chunks
    joined = "\n---\n".join(c.text for c in chunks)
    assert "Sentinel paragraph number 7." in joined
    for i in range(20):
        marker = f"Sentinel paragraph number {i}."
        hits = sum(marker in c.text for c in chunks)
        assert hits == 1, f"paragraph {i} appears in {hits} chunks"


def test_code_fence_not_split_and_hash_inside_is_not_heading():
    body = (
        "# Real Heading\n"
        "intro\n\n"
        "```python\n"
        "# this is a comment, not a heading\n"
        "\n"
        "def f():\n"
        "    return 1\n"
        "```\n"
        "after\n"
    )
    chunks = chunk_markdown("Company/Code.md", body)
    # the fence lives intact in exactly one chunk
    fence_chunks = [c for c in chunks if "def f():" in c.text]
    assert len(fence_chunks) == 1
    assert "# this is a comment, not a heading" in fence_chunks[0].text
    # the hash-comment never became a heading path
    assert all("this is a comment" not in c.heading_path for c in chunks)


def test_wikilinks_normalized():
    body = "# H\nSee [[Big Deal Decision|the decision]] and [[Q3 Pipeline]].\n"
    chunks = chunk_markdown("Company/H.md", body)
    text = "\n".join(c.text for c in chunks)
    assert "the decision" in text
    assert "Q3 Pipeline" in text
    assert "[[" not in text


def test_determinism():
    body = "# H\n" + "content " * 200 + "\n## H2\nmore\n"
    assert chunk_markdown("Company/H.md", body) == chunk_markdown("Company/H.md", body)


def test_empty_and_whitespace_yield_no_chunks():
    assert chunk_markdown("Company/E.md", "") == []
    assert chunk_markdown("Company/E.md", "\n\n   \n") == []
    assert chunk_markdown("Company/E.md", "---\nk: v\n---\n") == []


def test_pos_dense_ascending():
    body = "# A\n" + "x" * 300 + "\n## B\n" + "y" * 300 + "\n## C\n" + "z" * 300 + "\n"
    chunks = chunk_markdown("Company/D.md", body, target_chars=200)
    assert [c.pos for c in chunks] == list(range(len(chunks)))


def test_space_tagged_from_rel_path():
    chunks = chunk_markdown("People/bob/Memory.md", "# M\nbob note\n")
    assert chunks
    assert all(c.space == "People/bob" for c in chunks)


def test_embedding_input_carries_title_and_heading():
    c = Chunk(rel_path="Company/Decisions/Big Deal.md", space="Company",
              heading_path="Decision > Outcome", pos=0, text="we chose A")
    out = embedding_input(c)
    assert out.startswith("Big Deal — Decision > Outcome")
    assert "we chose A" in out


def test_tiny_sections_fold_together():
    body = "# A\nx\n## B\ny\n## C\nz\n### D\nw\n"
    chunks = chunk_markdown("Company/D.md", body, min_chars=64)
    # a pile of one-line headings collapses rather than making 4 micro-chunks
    assert len(chunks) < 4
