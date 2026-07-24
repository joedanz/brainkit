import subprocess
from pathlib import Path

from brain.doctor import Finding
from brain.schemas import Org, Person
from brain.triage import route_findings

from .test_cli import seed_meta  # noqa: F401  (used by later tasks' tests)

ALICE = Person(id="alice", name="Alice", roles=("admin",), teams=("sales",))
BOB = Person(id="bob", name="Bob", teams=("ops",))
ORG = Org(people={"alice": ALICE, "bob": BOB})


def test_personal_space_finding_routes_to_owner():
    f = Finding("warn", "unlinked-notes", "People/bob/Notes/Solo.md: no links",
                paths=("People/bob/Notes/Solo.md",))
    routed, unrouted = route_findings([f], ORG)
    assert routed == {"bob": [f]}
    assert unrouted == 0


def test_shared_space_and_unresolvable_route_to_admins():
    shared = Finding("warn", "intel", "Company/Intel/X.md: stale",
                     paths=("Company/Intel/X.md",))
    stray = Finding("warn", "orphan-files", "People/stray.md sits directly under People/",
                    paths=("People/stray.md",))  # People/stray is no org member
    routed, unrouted = route_findings([shared, stray], ORG)
    assert routed == {"alice": [shared, stray]}
    assert unrouted == 0


def test_two_path_finding_routes_to_both_owners():
    f = Finding("warn", "dup-exact", "People/bob/Notes/Copy.md and Company/Orig.md ...",
                paths=("People/bob/Notes/Copy.md", "Company/Orig.md"))
    routed, _ = route_findings([f], ORG)
    assert routed == {"bob": [f], "alice": [f]}


def test_error_infra_routes_to_admins_and_info_is_dropped():
    err = Finding("error", "symlinks", "x is a symlink")
    info_dup = Finding("info", "dup-near", "a and b cover similar content",
                       paths=("People/alice/Notes/a.md", "People/bob/Notes/b.md"))
    info_shares = Finding("info", "shares", "pending share")
    warn_infra = Finding("warn", "rule-paths", "rule 'X': missing")
    routed, unrouted = route_findings([err, info_dup, info_shares, warn_infra], ORG)
    assert routed == {"alice": [err]}  # info + warn-infra never routed
    assert unrouted == 0


def test_no_admins_counts_unrouted():
    org = Org(people={"bob": BOB})
    shared = Finding("warn", "intel", "Company/Intel/X.md: stale",
                     paths=("Company/Intel/X.md",))
    mine = Finding("warn", "unlinked-notes", "People/bob/Notes/Solo.md: no links",
                   paths=("People/bob/Notes/Solo.md",))
    routed, unrouted = route_findings([shared, mine], org)
    assert routed == {"bob": [mine]}
    assert unrouted == 1
