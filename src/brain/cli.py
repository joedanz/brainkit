"""brain — CLI for the multi-tenant company brain."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from brain.compiler import compile_all, compile_vault
from brain.cycle import run_cycle
from brain.doctor import run_doctor
from brain.promotions import PromotionError, approve, list_pending, reject, sweep
from brain.schemas import load_org, load_spaces
from brain.writeback import apply_writeback


def _load(master: Path):
    org = load_org(master / "_meta/org.yaml")
    rules = load_spaces(master / "_meta/spaces.yaml")
    return org, rules


def cmd_compile(args) -> int:
    master, out = Path(args.master), Path(args.out)
    org, rules = _load(master)
    if args.person:
        person = org.people.get(args.person)
        if person is None:
            print(f"unknown person: {args.person}", file=sys.stderr)
            return 1
        compile_vault(master, person, rules, out / person.id)
        print(f"compiled {person.id} -> {out / person.id}")
    else:
        results = compile_all(master, org, rules, out)
        for r in results:
            print(f"compiled {r.person_id}: {len(r.files)} files")
    return 0


def cmd_writeback(args) -> int:
    master, vault = Path(args.master), Path(args.vault)
    org, rules = _load(master)
    person = org.people.get(args.person)
    if person is None:
        print(f"unknown person: {args.person}", file=sys.stderr)
        return 1
    result = apply_writeback(master, vault, person, rules)
    if result.violations:
        print("REJECTED — nothing applied:", file=sys.stderr)
        for v in result.violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    print(f"applied {len(result.applied)} change(s)")
    return 0


def cmd_promotions(args) -> int:
    master = Path(args.master)
    try:
        if args.action == "list":
            for p in list_pending(master):
                print(f"{p.id}  from={p.person_id}  target={p.target_path}")
        elif args.action == "sweep":
            moved = sweep(master, today=date.today().isoformat())
            print(f"swept {len(moved)} draft(s) into the pending queue")
        elif args.action == "approve":
            target = approve(master, args.id, approver=args.approver,
                             date=date.today().isoformat())
            print(f"approved {args.id} -> {target}")
        elif args.action == "reject":
            reject(master, args.id, reason=args.reason)
            print(f"rejected {args.id}")
    except PromotionError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def cmd_init(args) -> int:
    from brain.templates import scaffold_master

    dest = Path(args.dir)
    if dest.exists() and any(dest.iterdir()):
        print(f"{dest} exists and is not empty", file=sys.stderr)
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    created = scaffold_master(dest, args.company)
    print(f"initialized {args.company} master vault at {dest} ({len(created)} files)")
    return 0


def cmd_cycle(args) -> int:
    report = run_cycle(Path(args.master), Path(args.out),
                       today=date.today().isoformat())
    if args.json:
        payload = asdict(report)
        payload["ok"] = report.ok
        print(json.dumps(payload, indent=2))
    else:
        for w in report.writebacks:
            line = f"writeback {w.person_id}: {w.status}"
            if w.status == "applied":
                line += f" ({w.applied} change(s))"
            print(line)
            for v in w.violations:
                print(f"  {v}", file=sys.stderr)
        print(f"swept {report.swept} draft(s); "
              f"compiled {report.compiled} vault(s); "
              f"{report.pending} promotion(s) pending")
    return 0 if report.ok else 1


def cmd_doctor(args) -> int:
    out_root = Path(args.out) if args.out else None
    findings = run_doctor(Path(args.master), out_root)
    errors = [f for f in findings if f.severity == "error"]
    if args.json:
        print(json.dumps({
            "ok": not errors,
            "findings": [asdict(f) for f in findings],
        }, indent=2))
    else:
        for f in findings:
            print(f"[{f.severity.upper():5}] {f.check}: {f.message}")
        warns = sum(1 for f in findings if f.severity == "warn")
        print(f"{len(errors)} error(s), {warns} warning(s), "
              f"{len(findings)} finding(s) total")
    return 1 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="brain")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("compile", help="compile per-person vaults from master")
    c.add_argument("--master", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--person")
    c.set_defaults(func=cmd_compile)

    w = sub.add_parser("writeback", help="validate and apply a person's edits")
    w.add_argument("--master", required=True)
    w.add_argument("--vault", required=True)
    w.add_argument("--person", required=True)
    w.set_defaults(func=cmd_writeback)

    p = sub.add_parser("promotions", help="manage the promotion queue")
    p.add_argument("action", choices=["list", "sweep", "approve", "reject"])
    p.add_argument("id", nargs="?")
    p.add_argument("--master", required=True)
    p.add_argument("--approver", default="")
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_promotions)

    i = sub.add_parser("init", help="scaffold a new company master vault")
    i.add_argument("dir")
    i.add_argument("--company", required=True)
    i.set_defaults(func=cmd_init)

    y = sub.add_parser("cycle", help="writeback all, sweep promotions, recompile")
    y.add_argument("--master", required=True)
    y.add_argument("--out", required=True)
    y.add_argument("--json", action="store_true")
    y.set_defaults(func=cmd_cycle)

    d = sub.add_parser("doctor", help="check master and compiled vaults for integrity issues")
    d.add_argument("--master", required=True)
    d.add_argument("--out")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
