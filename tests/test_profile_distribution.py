from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "deploy/agents-box/company-brain-profile"


def test_required_files_exist():
    for rel in ("README.md", "SOUL.md", "config.yaml",
                "skills/brain-protocol/SKILL.md"):
        assert (ROOT / rel).exists(), rel


def test_config_enforces_policies():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    # Built-in memory stays OFF: a native memory tool would win every
    # "remember this" over the vault, and facts stored there never sync.
    assert cfg["memory"]["memory_enabled"] is False
    assert cfg["memory"]["user_profile_enabled"] is False
    assert "provider" not in cfg["memory"]          # external memory OFF by policy
    assert cfg["skills"]["write_approval"] is True
    # Bundled llm-wiki stays OFF: it builds an unsynced wiki at ~/wiki,
    # competing with Company/Intel/ the same way native memory competed
    # with Memory.md.
    assert "llm-wiki" in cfg["skills"]["disabled"]
    assert "REPLACE_WITH_VAULT_PATH" in cfg["terminal"]["cwd"]
    # Gateway lifecycle pings stay OFF on every platform we deploy: a restart
    # here is always an operator action the user can do nothing about, so the
    # "gateway shutting down / back online" broadcast is pure noise in their
    # inbox. Pinned per platform because the flag has no global switch.
    for plat in ("telegram", "email"):
        assert cfg[plat]["gateway_restart_notification"] is False, plat
        # Top level, not under gateway.platforms — the nested path is hermes
        # #34067: written without complaint, then never read.
        assert "platforms" not in cfg.get("gateway", {})


def test_soul_and_skill_reference_the_vault_protocol():
    soul = (ROOT / "SOUL.md").read_text()
    assert "AGENTS.md" in soul
    skill = (ROOT / "skills/brain-protocol/SKILL.md").read_text()
    assert "promotion" in skill.lower()
    assert "Inbox" in skill
    # Memory.md stays a lean map; topic-sized detail splits into Notes/
    assert "Notes/<Topic>.md" in skill
    assert "lean overview" in skill
    # articles distill into the shared Intel wiki; no off-vault knowledge base
    # (noun-neutral: the vault's AGENTS.md names the shared space)
    assert "<shared space>/Intel/" in skill
    assert "Distill, never archive" in skill
    assert "as of YYYY-MM" in skill
    assert "captured YYYY-MM" in skill       # today's-date fallback when source undated
    assert "uploaded filename" in skill      # non-URL sources (PDF/screenshot)
    assert "mode: append" in skill           # additive page updates
    assert "mode: patch" in skill            # full-page revisions
    assert "no ~/wiki" in skill
    # typed-relation authoring: declare up/down/same/prev/next edges
    assert "## Relate" in skill
    assert "brain graph" in skill


def test_profile_skill_has_no_shared_space_literals():
    # The skill ships to every deployment; the shared top's name is per-vault
    # config, so the skill must defer to the vault's own AGENTS.md for it.
    text = (ROOT / "skills/brain-protocol/SKILL.md").read_text()
    assert "Company/" not in text


def test_profile_teaches_admission_not_only_routing():
    """The deployed profile must not teach a policy the vault's own AGENTS.md
    contradicts: both say record only what passes the gate."""
    soul = (ROOT / "SOUL.md").read_text()
    skill = (ROOT / "skills/brain-protocol/SKILL.md").read_text()

    assert "writing nothing is the ordinary outcome" in soul.lower()
    assert "## Admit" in skill
    for probe in ("**Durable**", "**Relevant**", "**New**", "**Attributable**"):
        assert probe in skill, probe
    # Needs-Routing is for the unplaceable, never for the rejected
    assert "never park what failed admission there" in skill
    assert "never as a place to put what" in soul


def test_profile_does_not_claim_keyword_only_retrieval():
    """`brain search` is hybrid (FTS5 + vector KNN + a PPR graph leg, fused by
    RRF — see src/brain/search.py). The skill claimed keyword-only, which both
    steers agents toward keyword-stuffed titles and invites them to read a
    search miss as proof the vault holds nothing on the subject — exactly the
    wrong inference under the 'search before you write' rule."""
    skill = (ROOT / "skills/brain-protocol/SKILL.md").read_text()
    assert "retrieval is keyword-based" not in skill
    assert "retrieval is hybrid" in skill
    assert "not as proof the vault" in skill
