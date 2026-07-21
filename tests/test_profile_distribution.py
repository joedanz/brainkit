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
    assert "REPLACE_WITH_VAULT_PATH" in cfg["terminal"]["cwd"]


def test_soul_and_skill_reference_the_vault_protocol():
    soul = (ROOT / "SOUL.md").read_text()
    assert "AGENTS.md" in soul
    skill = (ROOT / "skills/brain-protocol/SKILL.md").read_text()
    assert "promotion" in skill.lower()
    assert "Inbox" in skill
