"""A vendored copy of Hermes Agent's context-file filter.

Hermes Agent scans every context file it loads (AGENTS.md, CLAUDE.md,
SOUL.md, per-directory notes) with its threat patterns. Any match replaces
the WHOLE file with a one-line ``[BLOCKED: ...]`` notice, and the agent runs
without it. brainkit renders people's own words into those files (standing
corrections, space names, person names, the charter), so it checks them
against the same patterns first and renders around anything that would
match (contextgen, corrections, doctor).

What this is:

- The union of the patterns Hermes applies at scope "context" (its "all"
  plus "context" entries) in ``tools/threat_patterns.py`` at two pinned
  commits: 060779bb (2026-07-01) and 61154b6 (2026-09-16). Both have the same
  28 ids. 61154b6 narrowed three of them (translate_execute, exfil_curl,
  exfil_wget), and each narrowed form matches only text the older form
  already matches, so the older form is the union and is the one kept here.
- Its INVISIBLE_CHARS set, NFKC folding and MAX_SCAN_CHARS.
- ``blocks(text)``, with the semantics of ``scan_for_threats(text, "context")``,
  except that invisible-character ids come out sorted (Hermes emits them in
  set order).

Why vendored rather than imported: the brain box does not run Hermes, and
brainkit takes no runtime dependency on it. Hermes changed this file 19 times
in 4 months, in both directions. A union errs on the safe side: a false
positive costs a rephrase, a false negative costs a whole protocol. Phase 3
adds a companion check on the agents box, against each container's own
Hermes build, that catches drift between refreshes.

Refreshing, when Hermes changes tools/threat_patterns.py:

1. In a Hermes checkout, list the changes since the newest pin below:
   ``git log --oneline 61154b6..origin/main -- tools/threat_patterns.py``.
2. ``git show <commit>:tools/threat_patterns.py`` and list every
   ``_PATTERNS`` entry whose scope is "all" or "context".
3. A new id: add it to PATTERNS. A changed regex: keep whichever form matches
   more. If neither contains the other, keep both under the same id
   (``blocks`` reports an id once).
4. Compare INVISIBLE_CHARS and MAX_SCAN_CHARS: take the union and the larger.
5. Add the commit to the pins above and to PINNED_IDS in
   tests/test_hermes_filter.py, then run
   ``uv run pytest tests/test_hermes_filter.py``.

Licence: the patterns, INVISIBLE_CHARS and MAX_SCAN_CHARS below are copied
from Hermes Agent (https://github.com/NousResearch/hermes-agent):

    MIT License

    Copyright (c) 2025 Nous Research

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""

from __future__ import annotations

import re
import unicodedata

# Hermes scans only this much of a file. ROOT_LIMIT is well under it, so a
# whole protocol is always scanned.
MAX_SCAN_CHARS = 65_536

# Bounded filler between key words, exactly as Hermes writes it.
_FILLER = r"(?:\w+\s+){0,8}"

# (pattern_id, regex), matched case-insensitively against NFKC-folded text.
PATTERNS: tuple[tuple[str, str], ...] = (
    # Hermes scope "all"
    ("prompt_injection",
     rf"ignore\s+{_FILLER}(previous|all|above|prior)\s+{_FILLER}instructions"),
    ("sys_prompt_override", r"system\s+prompt\s+override"),
    ("disregard_rules",
     rf"disregard\s+{_FILLER}(your|all|any)\s+{_FILLER}(instructions|rules|guidelines)"),
    ("bypass_restrictions",
     (rf"act\s+as\s+(if|though)\s+{_FILLER}you\s+{_FILLER}(have\s+no|don't\s+have)\s+"
      rf"{_FILLER}(restrictions|limits|rules)")),
    ("html_comment_injection",
     r"<!--[^>]{0,512}(?:ignore|override|system|secret|hidden)[^>]{0,512}-->"),
    ("hidden_div", r"<\s*div\s+style\s*=\s*[\"'][^>]{0,2048}display\s*:\s*none"),
    # 060779bb form; 61154b6's narrower form matches a subset of this.
    ("translate_execute",
     r"translate\s+[^\n]{0,512}\s+into\s+[^\n]{0,512}\s+and\s+(execute|run|eval)"),
    ("deception_hide", rf"do\s+not\s+{_FILLER}tell\s+{_FILLER}the\s+user"),
    # 060779bb forms; 61154b6 dropped API and added a word boundary.
    ("exfil_curl", r"curl\s+[^\n]{0,2048}\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)"),
    ("exfil_wget", r"wget\s+[^\n]{0,2048}\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)"),
    ("read_secrets",
     r"cat\s+[^\n]{0,2048}(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)"),
    # Hermes scope "context"
    ("role_hijack", rf"you\s+are\s+{_FILLER}now\s+(?:a|an|the)\s+"),
    ("role_pretend", rf"pretend\s+{_FILLER}(you\s+are|to\s+be)\s+"),
    ("leak_system_prompt", rf"output\s+{_FILLER}(system|initial)\s+prompt"),
    ("remove_filters",
     rf"(respond|answer|reply)\s+without\s+{_FILLER}(restrictions|limitations|filters|safety)"),
    ("fake_update", rf"you\s+have\s+been\s+{_FILLER}(updated|upgraded|patched)\s+to"),
    ("identity_override", r"\bname\s+yourself\s+\w+"),
    ("c2_node_registration", r"register\s+(as\s+)?a?\s*node"),
    ("c2_heartbeat", r"(heartbeat|beacon|check[\s\-]?in)\s+(to|with)\s+"),
    ("c2_task_pull", r"pull\s+(down\s+)?(?:new\s+)?task(?:ing|s)?\b"),
    ("c2_network_connect", r"connect\s+to\s+the\s+network\b"),
    ("forced_action", r"you\s+must\s+(?:\w+\s+){0,3}(register|connect|report|beacon)\b"),
    ("anti_forensic_oneliner", r"only\s+use\s+one[\s\-]?liners?\b"),
    ("anti_forensic_disk",
     rf"never\s+{_FILLER}(?:create|write)\s+{_FILLER}(?:script|file)\s+{_FILLER}disk"),
    ("env_var_unset_agent", r"unset\s+\w*(?:CLAUDE|CODEX|HERMES|AGENT|OPENAI|ANTHROPIC)\w*"),
    ("known_c2_framework",
     r"\b(?:cobalt\s*strike|sliver|havoc|mythic|metasploit|brainworm)\b"),
    ("c2_explicit", r"\bc2\s+(?:server|channel|infrastructure|beacon)\b"),
    ("c2_explicit_long", r"\bcommand\s+and\s+control\b"),
)

# Zero-width space/non-joiner/joiner, word joiner, invisible times/separator/
# plus, BOM, LTR/RTL embedding, pop, overrides, and the four isolates.
INVISIBLE_CHARS = frozenset((
    "​",  # zero width space
    "‌",  # zero width non-joiner
    "‍",  # zero width joiner
    "⁠",  # word joiner
    "⁢",  # invisible times
    "⁣",  # invisible separator
    "⁤",  # invisible plus
    "﻿",  # byte order mark / zero width no-break space
    "‪",  # left-to-right embedding
    "‫",  # right-to-left embedding
    "‬",  # pop directional formatting
    "‭",  # left-to-right override
    "‮",  # right-to-left override
    "⁦",  # left-to-right isolate
    "⁧",  # right-to-left isolate
    "⁨",  # first strong isolate
    "⁩",  # pop directional isolate
))

_COMPILED = tuple((pid, re.compile(rx, re.IGNORECASE)) for pid, rx in PATTERNS)


def blocks(text: str) -> tuple[str, ...]:
    """The ids that would make Hermes drop a context file holding `text`.

    Empty means Hermes would load it. Invisible characters are checked on the
    raw text, since NFKC can strip some of them; patterns are matched on the
    NFKC-folded text, so full-width letters can't slip past.
    """
    if not text:
        return ()
    text = text[:MAX_SCAN_CHARS]
    hits = [f"invisible_unicode_U+{ord(ch):04X}"
            for ch in sorted(set(text) & INVISIBLE_CHARS)]
    folded = unicodedata.normalize("NFKC", text)
    hits.extend(pid for pid, rx in _COMPILED if rx.search(folded))
    return tuple(dict.fromkeys(hits))
