"""Integrity checks for a company brain: surface what otherwise fails silently.

Read-only by design: doctor never mutates master or any compiled vault.
Severity contract: "error" = invariant broken (exit 1), "warn" = probably a
mistake but nothing leaks (fail-closed side), "info" = normal state worth
seeing (e.g. edits awaiting writeback).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from brain.compiler import MANIFEST_NAME, _stem, extract_wikilinks
from brain.facts import parse_facts
from brain.frontmatter import split_frontmatter
from brain.promotions import PromotionError, _parse, _pending_dir, _validate_mode, _validate_target
from brain.resolver import RESERVED, _match_rule, can_read, enumerate_spaces, space_of_path
from brain.schemas import Org, SchemaError, SpaceRule, VaultConfig, load_config, load_org, load_spaces


@dataclass(frozen=True)
class Finding:
    severity: str  # "error" | "warn" | "info"
    check: str
    message: str


def _check_meta(master: Path) -> tuple[list[Finding], Org | None, tuple[SpaceRule, ...] | None]:
    findings: list[Finding] = []
    org = rules = None
    try:
        org = load_org(master / "_meta/org.yaml")
    except (SchemaError, OSError, yaml.YAMLError) as e:
        findings.append(Finding("error", "meta", f"org.yaml: {e}"))
    try:
        rules = load_spaces(master / "_meta/spaces.yaml")
    except (SchemaError, OSError, yaml.YAMLError) as e:
        findings.append(Finding("error", "meta", f"spaces.yaml: {e}"))
    return findings, org, rules


def _check_subjects(org: Org, rules: tuple[SpaceRule, ...]) -> list[Finding]:
    findings: list[Finding] = []
    teams = {t for p in org.people.values() for t in p.teams}
    roles = {r for p in org.people.values() for r in p.roles}
    for rule in rules:
        for subject in (*rule.read, *rule.write):
            if subject == "everyone" or "{name}" in subject:
                continue
            kind, _, value = subject.partition(":")
            if kind == "person" and value not in org.people:
                findings.append(Finding(
                    "error", "subjects",
                    f"rule {rule.path!r}: person {value!r} not in org.yaml"))
            elif kind == "team" and value not in teams:
                findings.append(Finding(
                    "warn", "subjects",
                    f"rule {rule.path!r}: no one is on team {value!r}"))
            elif kind == "role" and value not in roles:
                findings.append(Finding(
                    "warn", "subjects",
                    f"rule {rule.path!r}: no one holds role {value!r}"))
    return findings


def _check_rule_paths(master: Path, rules: tuple[SpaceRule, ...]) -> list[Finding]:
    findings: list[Finding] = []
    for rule in rules:
        base = rule.path[:-2] if rule.path.endswith("/*") else rule.path
        if not (master / base).is_dir():
            findings.append(Finding(
                "warn", "rule-paths",
                f"rule {rule.path!r}: {base!r} does not exist in master"))
    return findings


def _check_space_coverage(master: Path, rules: tuple[SpaceRule, ...]) -> list[Finding]:
    findings: list[Finding] = []
    for space in enumerate_spaces(master):
        rule, _ = _match_rule(space, rules)
        if rule is None:
            findings.append(Finding(
                "warn", "space-coverage",
                f"space {space!r} matches no rule — unreachable by everyone"))
    return findings


def _check_unreadable_spaces(master: Path, org: Org, rules: tuple[SpaceRule, ...]) -> list[Finding]:
    """A space whose rule resolves to zero readers is hidden from everyone —
    fail-closed, so nothing leaks, but the content silently compiles into no
    vault. The usual causes: a folder name that doesn't match any team/person
    id (matching is exact and case-sensitive — ``Teams/Sales`` vs a team called
    ``sales``), or a departed person's ``People/`` folder. Spaces matching no
    rule at all are `_check_space_coverage`'s job; an empty org is skipped
    because then every space is trivially unreadable and the warning is noise."""
    if not org.people:
        return []
    readers_of = _reader_index(org, rules)
    findings: list[Finding] = []
    for space in enumerate_spaces(master):
        rule, _ = _match_rule(space, rules)
        if rule is None or readers_of(space):
            continue
        findings.append(Finding(
            "warn", "unreadable-spaces",
            f"space {space!r} is readable by no one — its notes compile into no "
            f"vault; check the folder name against org ids (exact, case-sensitive) "
            f"or the subjects of rule {rule.path!r}"))
    return findings


def _check_orphan_files(master: Path) -> list[Finding]:
    """A .md placed directly under a nested top (Teams/, People/, the entity
    tree, or any other top-level dir) belongs to no space — those tops only
    form spaces from their subfolders — so the compiler copies it into
    nobody's vault. It vanishes silently. Company is itself a space, so files
    directly under it are fine and not checked here."""
    findings: list[Finding] = []
    for d in sorted(p for p in master.iterdir() if p.is_dir()):
        top = d.name
        if top in RESERVED or top.startswith(".") or top == "Company":
            continue
        for f in sorted(d.glob("*.md")):
            if f.is_file():
                findings.append(Finding(
                    "warn", "orphan-files",
                    f"{f.relative_to(master)} sits directly under {top}/ — not in "
                    f"any space, so it compiles into no vault; move it into a subfolder"))
    return findings


def _check_unlinked_notes(master: Path) -> list[Finding]:
    """Notes with no graph connections at all — no resolved wikilinks in or
    out (typed relations are wikilinks, so they count), no fact lines, and no
    mined structural edge (folder-index parent, date-sequence neighbor, or
    shared entity type). A note reachable only through mined structure is
    still reachable by brain_graph and PPR retrieval — flagging it would be
    a false positive — so this reuses the same miners the indexer's edge
    rebuild uses (brain.edges), duplicated over master's content files to
    keep doctor free of the indexer's store/embedding dependencies. Folders
    named Inbox are exempt: unprocessed captures are expected to be
    unlinked."""
    from brain.edges import date_edges, entity_edges, folder_edges, note_date
    from brain.facts import parse_entity

    findings: list[Finding] = []
    rels = _content_files(master)
    paths = set(rels)
    by_stem: dict[str, str] = {}
    for rel in sorted(rels):
        by_stem.setdefault(_stem(rel), rel)
    connected: set[str] = set()
    dated: dict[str, str] = {}
    entities: list[tuple[str, str]] = []
    for rel in rels:
        text = (master / rel).read_text(encoding="utf-8", errors="replace")
        if parse_facts(text):
            connected.add(rel)
        for raw in extract_wikilinks(text):
            target = _resolve_target(raw, paths, by_stem)
            if target and target != rel:
                connected.add(rel)
                connected.add(target)
        meta, _body = split_frontmatter(text)
        day = note_date(rel, meta)
        if day:
            dated[rel] = day
        ent = parse_entity(meta)
        if ent is not None:
            entities.append((rel, ent[0]))
    for src, dst, *_rest in folder_edges(rels):
        connected.add(src)
        connected.add(dst)
    for src, dst, *_rest in date_edges(dated):
        connected.add(src)
        connected.add(dst)
    for src, dst, *_rest in entity_edges(entities):
        connected.add(src)
        connected.add(dst)
    for rel in sorted(paths - connected):
        if "Inbox" in Path(rel).parts:
            continue
        findings.append(Finding(
            "warn", "unlinked-notes",
            f"{rel}: no links, relations, or facts connect this note — "
            "graph search can never reach it"))
    return findings


def _dup_exempt(rel: str) -> bool:
    """Transient and archival paths never count as duplicates: Inbox is
    pre-processing, Sessions is the processed archive, and Intel addenda are
    already flagged by _check_intel."""
    parts = Path(rel).parts
    if "Inbox" in parts or "Sessions" in parts:
        return True
    return bool(_ADDENDUM_RE.match(parts[-1]))


def _skeleton_pair(a: str, b: str) -> bool:
    """Same subpath inside two different personal spaces — every person owns
    the same scaffold (Memory.md, Notes/...), so identical templates across
    People/ spaces are structure, not duplication."""
    sa, sb = space_of_path(a), space_of_path(b)
    if not sa or not sb or sa == sb:
        return False
    if not (sa.startswith("People/") and sb.startswith("People/")):
        return False
    return a[len(sa):] == b[len(sb):]


def _cached_file_vectors(
    rels: list[str], texts: dict[str, str],
) -> dict[str, list[float]]:
    """File-level mean-pooled vectors, resolved from the shared embedding
    cache ONLY — the provider is never called (its constructor does no I/O
    and is used purely to learn the configured model name). A file with any
    chunk missing from the cache is dropped from this signal; any cache
    failure degrades to no signal at all. brain cycle's indexing keeps the
    cache warm, so in a live deployment coverage is near-total."""
    from brain.chunker import chunk_markdown, embedding_input
    from brain.dedup import mean_pool, unpack_vector
    from brain.embeddings import (
        EmbeddingCache, default_cache_path, provider_from_config)

    provider = provider_from_config()
    if provider is None:
        return {}
    cache_path = default_cache_path()
    if not cache_path.exists():
        return {}
    out: dict[str, list[float]] = {}
    try:
        cache = EmbeddingCache(cache_path)
    except Exception:
        return {}
    try:
        for rel in rels:
            chunks = chunk_markdown(rel, texts[rel])
            if not chunks:
                continue
            shas = [
                hashlib.sha256(
                    embedding_input(c).encode("utf-8")).hexdigest()
                for c in chunks]
            found = cache.get_many(shas, provider.model)
            if any(s not in found for s in shas):
                continue
            out[rel] = mean_pool([unpack_vector(found[s]) for s in shas])
    except Exception:
        return {}
    finally:
        cache.close()
    return out


def _check_duplicates(master: Path, org: Org, rules: tuple[SpaceRule, ...]) -> list[Finding]:
    """Duplicate and near-duplicate notes, in three tiers: identical bytes
    (dup-exact), colliding title stems (stem-collision — bare wikilinks
    resolve by stem, first match wins), and near-duplicate content
    (dup-near). Severity follows readership: a pair some person sees both
    sides of is real rot (warn — double retrieval votes, ambiguous links);
    a pair with no common reader never meets in any vault, so it is only a
    duplicated-effort hint (info: promotion candidate). Warn-on-disjoint is
    an invariant tested like the leak properties."""
    from brain.dedup import DUP_MIN_WORDS, normalize_text

    rels = [r for r in _content_files(master) if not _dup_exempt(r)]
    texts = {
        r: (master / r).read_text(encoding="utf-8", errors="replace")
        for r in rels}
    words = {r: normalize_text(texts[r]) for r in rels}
    substantive = [r for r in rels if len(words[r]) >= DUP_MIN_WORDS]
    readers_of = _reader_index(org, rules)

    def space_readers(rel: str) -> frozenset[str]:
        space = space_of_path(rel)
        return readers_of(space) if space else frozenset()

    findings: list[Finding] = []
    flagged: set[frozenset[str]] = set()

    def emit(a: str, b: str, check: str, warn_msg: str, info_msg: str) -> None:
        pair = frozenset((a, b))
        if pair in flagged or _skeleton_pair(a, b):
            return
        flagged.add(pair)
        if space_readers(a) & space_readers(b):
            findings.append(Finding("warn", check, warn_msg))
        else:
            findings.append(Finding("info", check, info_msg))

    # Tier 1: identical bytes. Chained pairs (a,b),(b,c) — one finding per
    # adjacent pair in a group is signal enough without O(n^2) noise.
    by_sha: dict[str, list[str]] = {}
    for rel in substantive:
        digest = hashlib.sha256(texts[rel].encode("utf-8")).hexdigest()
        by_sha.setdefault(digest, []).append(rel)
    for _digest, group in sorted(by_sha.items()):
        for a, b in zip(group, group[1:]):
            emit(
                a, b, "dup-exact",
                f"{a} and {b} have identical content — fold one into the "
                "other via a mode: patch promotion",
                f"{a} and {b} hold identical content in unshared spaces — "
                "promotion candidate")
        # Only adjacent pairs are emitted above, but every pair within an
        # identical group is still identical content — flag them all so
        # later tiers don't re-surface a non-adjacent pair as "near".
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                flagged.add(frozenset((a, b)))

    # Tier 2: stem collisions. Only pairs a common reader sees are amb-
    # iguous (both files land in that person's vault, where a bare
    # [[stem]] resolves first-match-wins); disjoint pairs are silent —
    # a stem match says nothing about content.
    by_stem: dict[str, list[str]] = {}
    for rel in rels:
        by_stem.setdefault(_stem(rel), []).append(rel)
    for stem, group in sorted(by_stem.items()):
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                pair = frozenset((a, b))
                if pair in flagged or _skeleton_pair(a, b):
                    continue
                if Path(a).name == "Home.md" and Path(b).name == "Home.md":
                    # Home.md is the per-space landing-page convention (the
                    # link map; _check_intel exempts it from the citation
                    # rule too) — two spaces each owning one is structure,
                    # like the personal skeletons, not link ambiguity.
                    continue
                if not (space_readers(a) & space_readers(b)):
                    continue
                flagged.add(pair)
                # _stem() lowercases for matching; the bracketed example in
                # the message should show a wikilink someone would actually
                # type, so it borrows the first file's on-disk casing.
                display_stem = Path(a).stem
                findings.append(Finding(
                    "warn", "stem-collision",
                    f"{a} and {b} share the title stem {stem!r} — a bare "
                    f"[[{display_stem}]] resolves to whichever sorts first; "
                    "rename one or link by full path"))

    # Tier 3a: lexical near-duplicates. LSH banding keeps the candidate set
    # near-linear; the signature estimate is the accept test.
    from brain.dedup import (
        DUP_COSINE, DUP_HAMMING_FRAC, DUP_JACCARD, band_keys, cosine,
        hamming, jaccard_estimate, minhash_signature, shingles, sign_bits)

    sigs: dict[str, tuple[int, ...]] = {}
    buckets: dict[tuple[int, tuple[int, ...]], list[str]] = {}
    for rel in substantive:
        sig = minhash_signature(shingles(words[rel]))
        if sig is None:
            continue
        sigs[rel] = sig
        for key in band_keys(sig):
            buckets.setdefault(key, []).append(rel)
    candidates: set[frozenset[str]] = set()
    for _key, group in sorted(buckets.items()):
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                candidates.add(frozenset((a, b)))
    for pair in sorted(candidates, key=sorted):
        a, b = sorted(pair)
        if pair in flagged:
            continue
        if jaccard_estimate(sigs[a], sigs[b]) >= DUP_JACCARD:
            emit(
                a, b, "dup-near",
                f"{a} and {b} are near-duplicates (text overlap) — fold one "
                "into the other via a mode: patch promotion",
                f"{a} and {b} cover similar content in unshared spaces — "
                "promotion candidate")

    # Tier 3b: semantic near-duplicates from cached embeddings. Sign-bit
    # hamming prefilters the O(n^2) pair loop; exact cosine confirms.
    vecs = _cached_file_vectors(substantive, texts)
    bits = {rel: sign_bits(v) for rel, v in vecs.items()}
    dim = len(next(iter(vecs.values()))) if vecs else 0
    max_ham = int(dim * DUP_HAMMING_FRAC)
    ordered = sorted(vecs)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            pair = frozenset((a, b))
            if pair in flagged:
                continue
            if hamming(bits[a], bits[b]) > max_ham:
                continue
            if cosine(vecs[a], vecs[b]) >= DUP_COSINE:
                emit(
                    a, b, "dup-near",
                    f"{a} and {b} are near-duplicates (semantic similarity) "
                    "— fold one into the other via a mode: patch promotion",
                    f"{a} and {b} cover similar content in unshared spaces — "
                    "promotion candidate")

    return findings


def _content_files(master: Path) -> list[str]:
    """All rel paths of .md files that live in a resolvable space."""
    rels: list[str] = []
    for f in sorted(master.rglob("*.md")):
        parts = f.relative_to(master).parts
        if parts[0] in RESERVED or parts[0].startswith("."):
            continue
        rel = f.relative_to(master).as_posix()
        if space_of_path(rel) is not None:
            rels.append(rel)
    return rels


def _resolve_target(target: str, paths: set[str], by_stem: dict[str, str]) -> str | None:
    """Resolve one raw wikilink target to a rel_path, or None if unresolved.
    Mirrors indexer._resolve_links; duplicated (not imported) to keep doctor free
    of the indexer's heavy embedding/store dependencies."""
    if "/" in target:
        for candidate in (target, target + ".md"):
            if candidate in paths:
                return candidate
    return by_stem.get(_stem(target))


def _reader_index(org: Org, rules: tuple[SpaceRule, ...]):
    """Return a memoized `readers_of(space) -> frozenset[person_id]`."""
    people = list(org.people.values())
    cache: dict[str, frozenset[str]] = {}

    def readers_of(space: str) -> frozenset[str]:
        if space not in cache:
            cache[space] = frozenset(
                p.id for p in people if can_read(space, p, rules))
        return cache[space]

    return readers_of


def _check_cross_space_refs(master: Path, org: Org, rules: tuple[SpaceRule, ...]) -> list[Finding]:
    """A note in space S that links to a note in space T leaks T's *name* to
    everyone who can read S — even though the compiler guarantees the *file*
    never crosses. If some reader of S cannot read T, that link exposes a note
    (client, deal, person) they aren't cleared to see. Warn, not error: no file
    crossed, but a human wrote a name into the wrong space. Unlinked plain-text
    mentions are caught separately by `_check_plain_refs`."""
    rels = _content_files(master)
    paths = set(rels)
    by_stem: dict[str, str] = {}
    for rel in rels:
        by_stem.setdefault(_stem(rel), rel)

    readers_of = _reader_index(org, rules)
    findings: list[Finding] = []
    for rel in rels:
        src_space = space_of_path(rel)
        # A personal space (People/<id>) has a single reader — its owner. A name
        # they reference in their own notes is exposed to no one else, so it can
        # never be a cross-person leak; skip to avoid "owner cannot see it" noise.
        if src_space.startswith("People/"):
            continue
        src_readers = readers_of(src_space)
        if not src_readers:
            continue
        flagged: set[str] = set()  # target spaces already reported for this file
        for target in extract_wikilinks((master / rel).read_text()):
            hit = _resolve_target(target, paths, by_stem)
            if hit is None:
                continue
            tgt_space = space_of_path(hit)
            if tgt_space is None or tgt_space == src_space or tgt_space in flagged:
                continue
            leaked = src_readers - readers_of(tgt_space)
            if leaked:
                flagged.add(tgt_space)
                findings.append(Finding(
                    "warn", "cross-refs",
                    f"{rel} links to {tgt_space!r}, but {len(leaked)} reader(s) of "
                    f"{src_space!r} cannot see it: {', '.join(sorted(leaked))} — "
                    f"the name leaks even though the file does not"))
    return findings


_WIKILINK_STRIP = re.compile(r"!?\[\[[^\]]*\]\]")


def _sensitive_names(master: Path, org: Org, readers_of) -> dict[str, str]:
    """Map each restricted space's leaf name to its space path, for spaces some
    person cannot read. Only names starting with a capital are kept: client and
    deal folders are proper nouns (``Vandenberg``), while team/person identifiers
    are lowercase (``sales``, ``marco``) and would collide with ordinary prose.
    This is the deliberate false-positive guard — the trade is that a lowercase
    client folder isn't scanned (name it ``Acme``, not ``acme``, to include it)."""
    roster = frozenset(org.people)
    names: dict[str, str] = {}
    for space in enumerate_spaces(master):
        leaf = space.split("/")[-1]
        if not leaf[:1].isupper():
            continue
        if readers_of(space) >= roster:  # everyone can read it -> not sensitive
            continue
        names.setdefault(leaf, space)
    return names


def _check_plain_refs(master: Path, org: Org, rules: tuple[SpaceRule, ...]) -> list[Finding]:
    """The unstructured sibling of `_check_cross_space_refs`: a restricted space's
    name written into shared prose *without* a wikilink still leaks. The compiler
    can only gate files, never redact text, so a client named in `Company/Memory`
    reaches everyone who reads Company. We scan for restricted proper-noun space
    names (whole word, case-sensitive) after stripping wikilinks (those are the
    cross-refs check's job). Heuristic by nature — hence warn, not error."""
    readers_of = _reader_index(org, rules)
    sensitive = _sensitive_names(master, org, readers_of)
    if not sensitive:
        return []
    matchers = {
        name: re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")
        for name in sensitive
    }
    findings: list[Finding] = []
    for rel in _content_files(master):
        src_space = space_of_path(rel)
        if src_space.startswith("People/"):
            continue  # sole reader is the owner — see _check_cross_space_refs
        src_readers = readers_of(src_space)
        if not src_readers:
            continue
        text = _WIKILINK_STRIP.sub(" ", (master / rel).read_text())
        flagged: set[str] = set()
        for name, home_space in sensitive.items():
            if home_space == src_space or home_space in flagged:
                continue
            leaked = src_readers - readers_of(home_space)
            if not leaked:
                continue
            if matchers[name].search(text):
                flagged.add(home_space)
                findings.append(Finding(
                    "warn", "plain-ref",
                    f"{rel} mentions {name!r} ({home_space}) in prose, but "
                    f"{len(leaked)} reader(s) of {src_space!r} cannot see that "
                    f"space: {', '.join(sorted(leaked))}"))
    return findings


def _check_facts(master: Path) -> list[Finding]:
    """Warn-only lint of fact lines and entity frontmatter. A malformed line
    is simply not a fact — nothing here ever blocks a compile."""
    from brain.facts import lint_facts, parse_entity

    findings: list[Finding] = []
    for rel in _content_files(master):
        text = (master / rel).read_text(encoding="utf-8", errors="replace")
        meta, _body = split_frontmatter(text)
        ent = parse_entity(meta)
        if ent is not None and not ent[0]:
            findings.append(Finding("warn", "facts", f"{rel}: empty entity type"))
        for line, msg in lint_facts(text):
            findings.append(Finding("warn", "facts", f"{rel}:{line}: {msg}"))
    return findings


def _check_fact_conflicts(master: Path) -> list[Finding]:
    """Two open facts about the same entity that duplicate or contradict each
    other — a double-landed ingest or a forgotten [until::]. Either way
    `brain facts` returns both lines and a reading agent gets a coin flip.
    Keys mirror query_facts_at: statement wikilinks (stem-resolved, raw text
    when unresolved) plus the host page when it carries entity frontmatter.
    Warn regardless of readership — unlike duplicate *notes*, a contradiction
    means one reader group holds a superseded fact even if no vault ever
    contains both lines."""
    from brain.facts import find_fact_conflicts, parse_entity

    rels = _content_files(master)
    paths = set(rels)
    by_stem: dict[str, str] = {}
    for rel in rels:
        by_stem.setdefault(_stem(rel), rel)

    entries = []
    for rel in rels:
        text = (master / rel).read_text(encoding="utf-8", errors="replace")
        meta, _body = split_frontmatter(text)
        is_entity = parse_entity(meta) is not None
        for fact in parse_facts(text):
            keys = {(_resolve_target(t, paths, by_stem) or t.casefold())
                    for t in fact.targets}
            if is_entity:
                keys.add(rel)
            if keys:
                entries.append((rel, fact, frozenset(keys)))

    findings: list[Finding] = []
    for kind, (rel_a, fa, keys_a), (rel_b, fb, keys_b) in find_fact_conflicts(entries):
        if kind == "dup":
            findings.append(Finding(
                "warn", "fact-dup",
                f'{rel_a}:{fa.line} ↔ {rel_b}:{fb.line}: duplicate open fact '
                f'"{fa.statement}" — delete one via write-back, or close the '
                f'older with [until::]'))
        else:
            about = sorted(keys_a & keys_b)[0]
            findings.append(Finding(
                "warn", "fact-conflict",
                f'{rel_a}:{fa.line} ↔ {rel_b}:{fb.line}: conflicting open '
                f'facts about [[{about}]]: "{fa.statement}" (from '
                f'{fa.from_date}) vs "{fb.statement}" (from {fb.from_date}) '
                f'— close the superseded fact with [until::]'))
    return findings


def _check_symlinks(master: Path) -> list[Finding]:
    findings: list[Finding] = []
    for p in sorted(master.rglob("*")):
        if ".git" in p.parts:
            continue
        if p.is_symlink():
            findings.append(Finding(
                "error", "symlinks",
                f"{p.relative_to(master)} is a symlink — compiler and writeback "
                "skip links, so this content is dead weight or an escape attempt"))
    return findings


def _check_promotions(master: Path) -> list[Finding]:
    findings: list[Finding] = []
    pending_dir = _pending_dir(master)
    valid_pending = 0
    if pending_dir.is_dir():
        for f in sorted(pending_dir.glob("*.md")):
            try:
                promo = _parse(f)
                _validate_target(promo.target_path)
                valid_pending += 1
            except (KeyError, ValueError, PromotionError) as e:
                findings.append(Finding(
                    "warn", "promotions",
                    f"pending/{f.name}: malformed, will never be approvable ({e})"))
    if valid_pending:
        findings.append(Finding(
            "info", "promotions",
            f"{valid_pending} promotion(s) awaiting approval"))

    # Drafts sweep() will silently skip forever: missing/invalid target-path.
    for f in sorted(master.glob("People/*/Promotions/*.md")):
        if f.is_symlink():
            continue
        rel = f.relative_to(master)
        meta, _ = split_frontmatter(f.read_text())
        if not meta:
            findings.append(Finding(
                "warn", "promotions", f"{rel}: draft has no frontmatter, sweep skips it"))
            continue
        try:
            _validate_target(meta.get("target-path", ""))
        except PromotionError as e:
            findings.append(Finding(
                "warn", "promotions", f"{rel}: sweep will never move it ({e})"))
            continue
        mode = meta.get("mode", "create")
        try:
            _validate_mode(mode)
        except PromotionError as e:
            findings.append(Finding(
                "warn", "promotions", f"{rel}: sweep will never move it ({e})"))
            continue
        if mode == "patch":
            t = master / meta["target-path"]
            if t.is_symlink():
                findings.append(Finding(
                    "warn", "promotions",
                    f"{rel}: patch draft targets a symlink — sweep will never queue it"))
            elif not t.is_file():
                findings.append(Finding(
                    "warn", "promotions",
                    f"{rel}: patch draft targets a missing page — sweep will never queue it"))
    return findings


def _check_webhook(master: Path, org: Org) -> list[Finding]:
    """Webhook intake is optional; when _meta/webhook.yaml exists, surface the
    config problems that would otherwise appear only when the receiver refuses
    to start (or, worse, when a provider's deliveries silently 404)."""
    import os

    from brain.webhook import CONFIG_NAME, WebhookConfigError, load_webhook_config

    path = master / "_meta" / CONFIG_NAME
    if not path.is_file():
        return []
    try:
        sources = load_webhook_config(path)
    except (WebhookConfigError, OSError) as e:
        return [Finding("error", "webhook", str(e))]

    findings: list[Finding] = []
    for s in sources:
        if s.person and s.person not in org.people:
            findings.append(Finding(
                "error", "webhook",
                f"source {s.id!r}: person {s.person!r} not in org.yaml"))
        if not os.environ.get(s.secret_env):
            findings.append(Finding(
                "warn", "webhook",
                f"source {s.id!r}: {s.secret_env} is unset in this environment — "
                "the receiver will refuse to start"))
    findings.append(Finding(
        "info", "webhook", f"{len(sources)} webhook source(s) configured"))
    return findings


def _check_compiled(master: Path, org, out_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for person in org.people.values():
        vault = out_root / person.id
        for tomb in (out_root / f".{person.id}.old", out_root / f".{person.id}.building"):
            if tomb.exists():
                findings.append(Finding(
                    "error", "compiled",
                    f"{tomb.name}: leftover from a crashed compile — "
                    "next compile will attempt recovery; investigate first"))
        if not vault.is_dir():
            findings.append(Finding(
                "warn", "compiled", f"{person.id}: no compiled vault yet"))
            continue
        if (vault / "_meta").exists():
            findings.append(Finding(
                "error", "compiled",
                f"{person.id}: _meta/ present inside compiled vault — "
                "SECURITY: server-only data leaked to a person"))
        manifest_path = vault / MANIFEST_NAME
        try:
            manifest = json.loads(manifest_path.read_text())
            drifted = 0
            for rel, sha in manifest["compiled"].items():
                f = vault / rel
                if not f.is_file():
                    drifted += 1
                elif hashlib.sha256(f.read_bytes()).hexdigest() != sha:
                    drifted += 1
        except (FileNotFoundError, ValueError, KeyError) as e:
            findings.append(Finding(
                "error", "compiled", f"{person.id}: unreadable manifest ({e})"))
            continue
        if drifted:
            findings.append(Finding(
                "info", "compiled",
                f"{person.id}: {drifted} file(s) awaiting writeback"))
    return findings


STALE_MONTHS = 12
_CITATION_RE = re.compile(r"(?:as of|captured)\s+(\d{4})-(0[1-9]|1[0-2])")
_ADDENDUM_RE = re.compile(r".+ [—-] updates \d{4}-(?:0[1-9]|1[0-2])\.md$")
_INTEL_DIR = "Company/Intel"


def _check_intel(master: Path, today: date | None = None) -> list[Finding]:
    """The Intel wiki's conventions fail silently: an unfolded addendum
    contradicts its merged page in search results, and a page nobody feeds
    quietly goes stale behind its own citations. Warn-only — nothing leaks
    and nothing blocks a compile. Home.md is the link map, exempt from the
    citation rule; addenda are exempt from staleness (already flagged)."""
    intel = master / _INTEL_DIR
    if not intel.is_dir():
        return []
    today = today or date.today()
    now_m = today.year * 12 + today.month - 1
    findings: list[Finding] = []
    for f in sorted(intel.rglob("*.md")):
        if f.is_symlink():
            continue
        rel = f.relative_to(master).as_posix()
        if _ADDENDUM_RE.match(f.name):
            findings.append(Finding(
                "warn", "intel",
                f"{rel}: unfolded addendum — fold it into its page and delete "
                "it, or have the agent resubmit as a mode: patch promotion"))
            continue
        if f.name == "Home.md":
            continue
        months = [int(y) * 12 + int(m) - 1
                  for y, m in _CITATION_RE.findall(f.read_text())]
        if not months:
            findings.append(Finding(
                "warn", "intel",
                f"{rel}: no dated citations — every Intel claim needs "
                "`[source](URL), as of YYYY-MM` or `captured YYYY-MM`"))
        elif now_m - max(months) > STALE_MONTHS:
            newest = max(months)
            findings.append(Finding(
                "warn", "intel",
                f"{rel}: stale — newest citation "
                f"{newest // 12:04d}-{newest % 12 + 1:02d} is over "
                f"{STALE_MONTHS} months old"))
    return findings


def _check_created_clients(master: Path, config: VaultConfig = None) -> list[Finding]:
    """Auto-created client spaces, surfaced for admin review (rename/merge/revoke).
    Informational: self-service creation is normal, but a human should be able to
    see the roster grow."""
    from brain.clients import _created_log

    if config is None:
        config = VaultConfig()
    log = _created_log(master)
    if not log.is_file():
        return []
    findings: list[Finding] = []
    for line in log.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        date_, owner, name = parts[0], parts[1], parts[2]
        findings.append(Finding(
            "info", "clients",
            f"{config.entities}/{name} — created by {owner} on {date_} (self-service)"))
    return findings


def _check_pending_shares(master: Path) -> list[Finding]:
    """Share requests awaiting approval, surfaced for the admin. Informational:
    the queue is the human gate working as designed, but it should be visible."""
    from brain.shares import list_pending_shares

    findings: list[Finding] = []
    for s in list_pending_shares(master):
        findings.append(Finding(
            "info", "shares",
            f"{s.get('space', '?')} → {s.get('share-with', '?')} "
            f"({s.get('access', '?')}) requested by {s.get('from', '?')} "
            f"on {s.get('created', '?')} — awaiting approval"))
    return findings


def _check_delegated_decisions(master: Path) -> list[Finding]:
    """Share decisions made in-vault (via: delegated) in the last 30 days,
    surfaced for admin review — the audit counterweight to delegating the
    human gate into deciders' vaults."""
    from datetime import date as _date, timedelta

    from brain.frontmatter import split_frontmatter

    cutoff = _date.today() - timedelta(days=30)
    findings: list[Finding] = []
    for state, key, by_key, verb in (
        ("approved", "approved-on", "approved-by", "approved"),
        ("rejected", "rejected-on", "rejected-by", "rejected"),
    ):
        d = master / "_meta/shares" / state
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            try:
                meta, _ = split_frontmatter(f.read_text())
            except (KeyError, ValueError, UnicodeDecodeError):
                continue
            if not meta or meta.get("via") != "delegated":
                continue
            try:
                when = _date.fromisoformat(str(meta.get(key, "")))
            except ValueError:
                continue
            if when < cutoff:
                continue
            findings.append(Finding(
                "info", "shares",
                f"{meta.get('space', '?')} → {meta.get('share-with', '?')} "
                f"{verb} by {meta.get(by_key, '?')} on {when.isoformat()} "
                f"(delegated)"))
    return findings


def run_doctor(master: Path, out_root: Path | None = None) -> list[Finding]:
    findings, org, rules = _check_meta(master)
    if org is None or rules is None:
        return findings  # dependent checks are meaningless on broken meta
    try:
        config = load_config(master)
    except SchemaError as e:
        findings.append(Finding("error", "meta", f"config.yaml: {e}"))
        config = VaultConfig()
    findings += _check_subjects(org, rules)
    findings += _check_rule_paths(master, rules)
    findings += _check_space_coverage(master, rules)
    findings += _check_unreadable_spaces(master, org, rules)
    findings += _check_orphan_files(master)
    findings += _check_unlinked_notes(master)
    findings += _check_duplicates(master, org, rules)
    findings += _check_cross_space_refs(master, org, rules)
    findings += _check_plain_refs(master, org, rules)
    findings += _check_facts(master)
    findings += _check_fact_conflicts(master)
    findings += _check_symlinks(master)
    findings += _check_promotions(master)
    findings += _check_created_clients(master, config)
    findings += _check_pending_shares(master)
    findings += _check_delegated_decisions(master)
    findings += _check_intel(master)
    findings += _check_webhook(master, org)
    if out_root is not None:
        findings += _check_compiled(master, org, out_root)
    return findings
