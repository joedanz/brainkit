from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "templates/company-brain-profile"


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
    assert "Company/Intel/" in skill
    assert "Distill, never archive" in skill
    assert "as of YYYY-MM" in skill
    assert "captured YYYY-MM" in skill       # today's-date fallback when source undated
    assert "uploaded filename" in skill      # non-URL sources (PDF/screenshot)
    assert "updates YYYY-MM.md" in skill
    assert "no ~/wiki" in skill
