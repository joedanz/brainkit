from brain.frontmatter import split_frontmatter


def test_no_frontmatter_passthrough():
    text = "# Just a heading\n\nbody text\n"
    meta, body = split_frontmatter(text)
    assert meta == {}
    assert body == text


def test_normal_block():
    text = "---\nkey: value\nfrom: alice\n---\nthe body\n"
    meta, body = split_frontmatter(text)
    assert meta == {"key": "value", "from": "alice"}
    assert body == "the body\n"


def test_value_containing_colon_space():
    text = "---\ntarget-path: Clients/acme: overview\n---\nbody\n"
    meta, body = split_frontmatter(text)
    # partition on first ": " — the rest of the value is preserved verbatim.
    assert meta == {"target-path": "Clients/acme: overview"}
    assert body == "body\n"


def test_empty_block():
    text = "---\n\n---\nbody\n"
    meta, body = split_frontmatter(text)
    assert meta == {}
    assert body == "body\n"


def test_body_preserved_byte_for_byte():
    # Promotion round-trip depends on body bytes surviving untouched, including
    # a body that itself contains a --- line.
    body_in = "line one\n---\nline after a rule\n"
    text = f"---\nk: v\n---\n{body_in}"
    meta, body = split_frontmatter(text)
    assert meta == {"k": "v"}
    assert body == body_in


def test_only_opening_delimiter_is_not_frontmatter():
    # A single leading --- with no closing delimiter is not a frontmatter block.
    text = "---\nnot really frontmatter\n"
    meta, body = split_frontmatter(text)
    assert meta == {}
    assert body == text
