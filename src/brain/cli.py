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
from brain.ingest import IngestError, ingest_note
from brain.promotions import PromotionError, approve, list_pending, reject, sweep
from brain.schemas import load_org, load_spaces
from brain.writeback import ManifestError, apply_writeback


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
        compile_vault(
            master, person, rules, out / person.id, today=date.today().isoformat()
        )
        print(f"compiled {person.id} -> {out / person.id}")
    else:
        results = compile_all(master, org, rules, out, today=date.today().isoformat())
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
    try:
        result = apply_writeback(master, vault, person, rules)
    except ManifestError as e:
        print(f"cannot write back: {e}", file=sys.stderr)
        return 1
    if result.violations:
        print("REJECTED — nothing applied:", file=sys.stderr)
        for v in result.violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    print(f"applied {len(result.applied)} change(s)")
    return 0


def cmd_promotions(args) -> int:
    from brain.schemas import SchemaError

    master = Path(args.master)
    try:
        if args.action == "list":
            for p in list_pending(master):
                print(f"{p.id}  from={p.person_id}  target={p.target_path}")
        elif args.action == "sweep":
            moved = sweep(master, today=date.today().isoformat())
            print(f"swept {len(moved)} draft(s) into the pending queue")
        elif args.action == "approve":
            if not args.approver:
                print("--approver is required for approve", file=sys.stderr)
                return 2
            target = approve(master, args.id, approver=args.approver,
                             date=date.today().isoformat())
            print(f"approved {args.id} -> {target}")
        elif args.action == "reject":
            reject(master, args.id, reason=args.reason,
                   date=date.today().isoformat())
            print(f"rejected {args.id}")
    except (PromotionError, SchemaError) as e:
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


def cmd_ingest(args) -> int:
    master = Path(args.master)
    org, rules = _load(master)

    if args.sender is not None:
        person = org.person_by_email(args.sender)
        if person is None:
            print(f"no person with email {args.sender!r} — refusing to ingest",
                  file=sys.stderr)
            return 1
    else:
        person = org.people.get(args.person)
        if person is None:
            print(f"unknown person: {args.person}", file=sys.stderr)
            return 1

    if args.file:
        try:
            body = Path(args.file).read_text()
        except (OSError, UnicodeDecodeError) as e:
            print(f"cannot read {args.file}: {e}", file=sys.stderr)
            return 1
    else:
        body = sys.stdin.read()
    if not body.strip():
        print("empty note — nothing to ingest", file=sys.stderr)
        return 1

    created = (args.date or date.today()).isoformat()
    if args.title:
        title = args.title
    elif args.file:
        title = Path(args.file).stem
    else:
        first = next((ln.strip().lstrip("# ").strip()
                      for ln in body.splitlines() if ln.strip()), "")
        title = first or "note"
    sender = args.sender if args.sender is not None else (person.email or person.id)
    original_name = Path(args.file).name if args.file else ""

    try:
        result = ingest_note(
            master, person, rules, body,
            title=title, source=args.source, sender=sender,
            created=created, original_name=original_name,
        )
    except IngestError as e:
        print(str(e), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({**asdict(result), "ok": True}, indent=2))
    else:
        print(f"ingested {result.rel_path} (source={result.source})")
    return 0


def cmd_webhook(args) -> int:
    from brain.webhook import CONFIG_NAME, WebhookConfigError, run_webhook_server

    master = Path(args.master)
    if not (master / "_meta" / CONFIG_NAME).is_file():
        print(f"no _meta/{CONFIG_NAME} under {master} — declare your sources there first",
              file=sys.stderr)
        return 1
    try:
        return run_webhook_server(master, host=args.host, port=args.port,
                                  max_body=args.max_body_mb * 1024 * 1024)
    except WebhookConfigError as e:
        print(f"cannot start webhook receiver: {e}", file=sys.stderr)
        return 1


def cmd_cycle(args) -> int:
    report = run_cycle(Path(args.master), Path(args.out),
                       today=date.today().isoformat(), index=args.index)
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
        if args.index:
            print(f"indexed {report.indexed} vault(s)")
        for w in report.index_warnings:
            print(f"  index warning: {w}", file=sys.stderr)
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


def cmd_index(args) -> int:
    from brain.embeddings import EmbeddingCache, default_cache_path, provider_from_config
    from brain.indexer import build_index

    provider = provider_from_config()
    cache = EmbeddingCache(default_cache_path()) if provider else None
    try:
        report = build_index(Path(args.vault), provider=provider, cache=cache, full=args.full)
    except ManifestError as e:
        print(f"cannot index: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({**asdict(report), "ok": True}, indent=2))
    else:
        print(f"indexed {report.files_indexed} file(s) "
              f"({report.files_removed} removed, {report.files_unchanged} unchanged); "
              f"embedded {report.chunks_embedded} chunk(s) "
              f"({report.chunks_from_cache} cached) [{report.mode}]")
        for w in report.warnings:
            print(f"  warning: {w}", file=sys.stderr)
    return 0


def cmd_search(args) -> int:
    from brain.embeddings import provider_from_config
    from brain.search import search_index

    vault = Path(args.vault)
    index = vault / ".brain" / "index.db"
    if not index.exists():
        print(f"no index at {index} — run: brain index --vault {args.vault}", file=sys.stderr)
        return 1
    provider = None if args.keyword_only else provider_from_config()
    report = search_index(
        vault, args.query, k=args.k, provider=provider,
        keyword_only=args.keyword_only, center=args.center,
    )
    if args.json:
        print(json.dumps({
            "query": report.query,
            "mode": report.mode,
            "hits": [asdict(h) for h in report.hits],
            "warnings": report.warnings,
        }, indent=2))
    else:
        if not report.hits:
            print("no results")
        for i, h in enumerate(report.hits, 1):
            loc = h.rel_path + (f" — {h.heading_path}" if h.heading_path else "")
            print(f"{i}. {loc}  ({h.score})")
            print(f"   {h.snippet}")
        for w in report.warnings:
            print(f"  warning: {w}", file=sys.stderr)
    return 0


def cmd_mcp(args) -> int:
    from brain.mcp import serve

    serve(Path(args.vault))
    return 0


def cmd_status(args) -> int:
    import yaml

    from brain.schemas import SchemaError
    from brain.stats import (
        collect_master_stats,
        collect_vault_stats,
        format_master_status,
        format_vault_status,
    )

    if args.vault and args.out:
        print("--out only applies to the admin lens (--master)", file=sys.stderr)
        return 2

    try:
        if args.vault:
            stats = collect_vault_stats(Path(args.vault), include_graph=False)
            text = format_vault_status(stats)
        else:
            out_root = Path(args.out) if args.out else None
            stats = collect_master_stats(Path(args.master), out_root)
            text = format_master_status(stats)
    except ManifestError as e:
        print(f"cannot read vault: {e}", file=sys.stderr)
        return 1
    except (SchemaError, OSError, yaml.YAMLError) as e:
        print(f"cannot read master meta: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({**asdict(stats), "ok": True}, indent=2))
    else:
        print(text)
    return 0


def cmd_dashboard(args) -> int:
    if args.vault and args.out:
        print("--out only applies to the admin lens (--master)", file=sys.stderr)
        return 2

    # --html writes a static, self-contained snapshot (the original behavior).
    # Without it, the default is a live server that updates as the brain changes.
    if args.html:
        return _dashboard_static(args)
    return _dashboard_serve(args)


def _dashboard_static(args) -> int:
    import webbrowser

    import yaml

    from brain.dashboard import write_dashboard
    from brain.schemas import SchemaError
    from brain.stats import collect_master_stats, collect_vault_stats

    try:
        if args.vault:
            stats = collect_vault_stats(Path(args.vault), include_graph=True)
        else:
            out_root = Path(args.out) if args.out else None
            stats = collect_master_stats(Path(args.master), out_root)
    except ManifestError as e:
        print(f"cannot read vault: {e}", file=sys.stderr)
        return 1
    except (SchemaError, OSError, yaml.YAMLError) as e:
        print(f"cannot read master meta: {e}", file=sys.stderr)
        return 1

    path = write_dashboard(stats, Path(args.html))
    print(f"wrote {path}")
    for w in stats.warnings:
        print(f"  warning: {w}", file=sys.stderr)
    if args.open_browser:
        webbrowser.open(path.resolve().as_uri())
    return 0


def _dashboard_serve(args) -> int:
    from brain.server import run_server
    from brain.watch import Lens

    # Fail fast on an obviously wrong target rather than serving only errors.
    if args.vault:
        if not Path(args.vault).is_dir():
            print(f"cannot read vault: {args.vault} is not a directory", file=sys.stderr)
            return 1
        lens = Lens(kind="vault", vault=Path(args.vault))
    else:
        if not (Path(args.master) / "_meta" / "org.yaml").is_file():
            print(f"cannot read master: no _meta/org.yaml under {args.master}",
                  file=sys.stderr)
            return 1
        lens = Lens(kind="master", master=Path(args.master),
                    out_root=Path(args.out) if args.out else None)
    return run_server(lens, host=args.host, port=args.port,
                      open_browser=not args.no_open)


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

    g = sub.add_parser("ingest", help="write one note into a person's Inbox in master")
    g.add_argument("--master", required=True)
    who = g.add_mutually_exclusive_group(required=True)
    who.add_argument("--person")
    who.add_argument("--from", dest="sender", metavar="EMAIL",
                     help="resolve the person by org.yaml email (unknown senders are refused)")
    g.add_argument("--file", help="note content to ingest (default: read stdin)")
    g.add_argument("--title", help="note title (default: from --file name or first line)")
    g.add_argument("--source", default="manual",
                   help="intake channel, e.g. email|chat|voice|upload|manual")
    g.add_argument("--date", type=date.fromisoformat, default=None,
                   help="provenance date, YYYY-MM-DD (default: today)")
    g.add_argument("--json", action="store_true")
    g.set_defaults(func=cmd_ingest)

    wh = sub.add_parser(
        "webhook",
        help="serve the signed webhook intake receiver "
             "(sources from _meta/webhook.yaml; notes land via brain ingest)")
    wh.add_argument("--master", required=True)
    wh.add_argument("--host", default="127.0.0.1",
                    help="bind address (default: %(default)s; non-loopback needs "
                         "TLS in front — requests authenticate, transport doesn't)")
    wh.add_argument("--port", type=int, default=8766,
                    help="port (default: %(default)s; 0 picks a free port)")
    wh.add_argument("--max-body-mb", type=int, default=5,
                    help="largest accepted request body in MB (default: %(default)s)")
    wh.set_defaults(func=cmd_webhook)

    y = sub.add_parser("cycle", help="writeback all, sweep promotions, recompile")
    y.add_argument("--master", required=True)
    y.add_argument("--out", required=True)
    y.add_argument("--json", action="store_true")
    y.add_argument("--index", action="store_true",
                   help="also refresh each vault's search index after compile")
    y.set_defaults(func=cmd_cycle)

    ix = sub.add_parser("index", help="build/refresh the search index for a compiled vault")
    ix.add_argument("--vault", required=True)
    ix.add_argument("--full", action="store_true", help="rebuild from scratch")
    ix.add_argument("--json", action="store_true")
    ix.set_defaults(func=cmd_index)

    sp = sub.add_parser("search", help="hybrid search over a compiled vault's index")
    sp.add_argument("query")
    sp.add_argument("--vault", required=True)
    sp.add_argument("--k", type=int, default=8)
    sp.add_argument("--keyword-only", action="store_true")
    sp.add_argument("--center", metavar="REL_PATH", default=None,
                    help="rank results near this note in the wikilink graph higher")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_search)

    mc = sub.add_parser(
        "mcp",
        help="run a stdio MCP server over a vault "
             "(register: claude mcp add brain -- brain mcp --vault ~/brain)")
    mc.add_argument("--vault", required=True)
    mc.set_defaults(func=cmd_mcp)

    st = sub.add_parser("status", help="counts, freshness and health for a vault or the whole company")
    lens = st.add_mutually_exclusive_group(required=True)
    lens.add_argument("--vault", help="a compiled per-person vault (user lens)")
    lens.add_argument("--master", help="the master vault (admin lens)")
    st.add_argument("--out", help="compiled output root (admin lens; enables per-person checks)")
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=cmd_status)

    db = sub.add_parser(
        "dashboard",
        help="serve a live dashboard (default) or write a static HTML snapshot with --html")
    dlens = db.add_mutually_exclusive_group(required=True)
    dlens.add_argument("--vault", help="a compiled per-person vault (user lens)")
    dlens.add_argument("--master", help="the master vault (admin lens)")
    db.add_argument("--out", help="compiled output root (admin lens; enables per-person checks)")
    db.add_argument("--html", metavar="PATH",
                    help="write a static self-contained HTML file to PATH and exit "
                         "(instead of serving the live dashboard)")
    db.add_argument("--host", default="127.0.0.1",
                    help="live server bind address (default: %(default)s; "
                         "WARNING: a non-loopback host exposes the vault with no auth)")
    db.add_argument("--port", type=int, default=8765,
                    help="live server port (default: %(default)s; 0 picks a free port)")
    db.add_argument("--no-open", action="store_true",
                    help="do not open a browser when serving the live dashboard")
    db.add_argument("--open", action="store_true", dest="open_browser",
                    help="open the file in a browser (static --html mode only)")
    db.set_defaults(func=cmd_dashboard)

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
