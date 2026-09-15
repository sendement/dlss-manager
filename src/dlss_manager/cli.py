from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .components import COMPONENTS
from .library import UnknownComponentError
from .manager import Manager
from .optiscaler import PROXY_DLL_CHOICES, ExtractionError, OptiScalerError


def _print_table(rows: list[list[str]], headers: list[str]) -> None:
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for r in rows:
        print(fmt.format(*[str(c) for c in r]))


def cmd_scan(args, m: Manager) -> None:
    games = m.scan_all(collect_into_library=not args.no_collect)
    print(f"Scanned {len(games)} installed game(s).")


def cmd_list(args, m: Manager) -> None:
    rows = []
    for game in m.db.all_games():
        comps = m.db.components_for_game(game["app_id"])
        if not comps:
            continue
        comp_str = ", ".join(f"{c['component_key']}={c['version']}" for c in comps)
        rows.append([game["app_id"], game["name"], comp_str])
    if not rows:
        print("No DLSS components found yet. Run 'scan' first.")
        return
    _print_table(rows, ["app_id", "game", "components"])


def cmd_library(args, m: Manager) -> None:
    rows = m.library_versions(args.component)
    if not rows:
        print("Library is empty. Run 'scan' or 'import' first.")
        return
    _print_table(
        [[r["id"], r["component_key"], r["version"], r["sha256"][:12], r["source"]] for r in rows],
        ["id", "component", "version", "sha256", "source"],
    )


def cmd_import(args, m: Manager) -> None:
    path = Path(args.path)
    if not path.is_file():
        print(f"error: {path} is not a file", file=sys.stderr)
        sys.exit(1)
    try:
        entry = m.import_dll(path)
    except UnknownComponentError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    status = "added" if entry.is_new else "already in library"
    print(f"{status}: {entry.component.display_name} {entry.version} -> {entry.path}")


def cmd_apply(args, m: Manager) -> None:
    try:
        result = m.apply_version(args.app_id, args.component, args.library_id)
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(
        f"Applied change #{result.change_id}: "
        f"{result.previous_version} -> {result.new_version} "
        f"(backup: {result.backup_path})"
    )


def cmd_history(args, m: Manager) -> None:
    rows = m.db.active_changes(app_id=args.app_id)
    if not rows:
        print("No active applied changes.")
        return
    _print_table(
        [
            [r["id"], r["game_name"], r["component_key"], r["previous_version"], r["new_version"], r["applied_at"]]
            for r in rows
        ],
        ["id", "game", "component", "from", "to", "applied_at"],
    )


def cmd_rollback(args, m: Manager) -> None:
    try:
        m.rollback_change(args.change_id)
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Rolled back change #{args.change_id}.")


def cmd_rollback_all(args, m: Manager) -> None:
    n = m.rollback_all()
    print(f"Restored {n} file(s) to their pre-app state.")


def cmd_clean_logs(args, m: Manager) -> None:
    artifacts = m.clean_wrapper_artifacts(app_id=args.app_id, dry_run=not args.apply)
    if not artifacts:
        print("No wrapper log/state files found.")
        return
    verb = "Removed" if args.apply else "Would remove"
    for p in artifacts:
        print(f"{verb}: {p}")
    if not args.apply:
        print(f"\n{len(artifacts)} file(s). Re-run with --apply to actually delete them.")


def cmd_clean_library(args, m: Manager) -> None:
    notes = m.dedupe_library()
    if not notes:
        print("Library is already clean.")
        return
    for n in notes:
        print(n)


def _find_release(m: Manager, tag: str | None, source_key: str = "official"):
    if tag is None:
        return m.latest_optiscaler_release(source_key=source_key)
    for r in m.optiscaler_releases(source_key=source_key, limit=30):
        if r.tag == tag:
            return r
    print(f"error: release {tag!r} not found in the last 30 {source_key} releases", file=sys.stderr)
    sys.exit(1)


def cmd_optiscaler_sources(args, m: Manager) -> None:
    for s in m.optiscaler_sources():
        print(f"{s.key}\t{s.repo}\t{s.label}")
        if s.note:
            for line in s.note.splitlines():
                print(f"\t{line}")


def cmd_optiscaler_releases(args, m: Manager) -> None:
    try:
        releases = m.optiscaler_releases(source_key=args.source, limit=args.limit)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    _print_table(
        [[r.tag, r.published_at or "?", f"{r.asset_size / 1_048_576:.1f} MB"] for r in releases],
        ["tag", "published_at", "asset"],
    )


def cmd_optiscaler_targets(args, m: Manager) -> None:
    try:
        targets = m.suggest_optiscaler_targets(args.app_id)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    if not targets:
        print("No candidate folders found (no .exe under the install dir?).")
        return
    for i, t in enumerate(targets):
        print(f"[{i}] {t}")


def cmd_optiscaler_install(args, m: Manager) -> None:
    from .optiscaler import SOURCES

    source = SOURCES.get(args.source)
    if source is None:
        print(f"error: unknown source {args.source!r}", file=sys.stderr)
        sys.exit(1)
    if source.note:
        print(f"--- {source.label} ---")
        print(source.note)
        print("---")

    try:
        release = _find_release(m, args.version, source_key=args.source)
        target_dir = Path(args.target) if args.target else m.suggest_optiscaler_targets(args.app_id)[0]
        result = m.install_optiscaler(
            args.app_id,
            target_dir,
            proxy_filename=args.proxy,
            release=release,
            source_key=args.source,
            overwrite_conflict=args.overwrite,
        )
    except (ValueError, FileExistsError, OptiScalerError, ExtractionError, IndexError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Installed {source.label} {release.tag} -> {result.target_dir} as {result.proxy_filename}")
    if result.conflict_backup_path:
        print(f"(existing {result.proxy_filename} backed up to {result.conflict_backup_path})")


def cmd_optiscaler_list(args, m: Manager) -> None:
    rows = m.db.active_optiscaler_installs()
    if not rows:
        print("No active OptiScaler installs.")
        return
    _print_table(
        [[r["id"], r["game_name"], r["source_key"], r["version"], r["proxy_filename"], r["target_dir"]] for r in rows],
        ["id", "game", "source", "version", "proxy", "target_dir"],
    )


def cmd_optiscaler_check(args, m: Manager) -> None:
    try:
        updates = m.check_optiscaler_updates()
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    if not updates:
        print("All OptiScaler installs are up to date.")
        return
    _print_table(
        [[row["id"], row["game_name"], row["version"], latest.tag] for row, latest in updates],
        ["id", "game", "installed", "latest"],
    )


def cmd_optiscaler_update(args, m: Manager) -> None:
    row = m.db.optiscaler_install(args.install_id)
    if row is None:
        print(f"error: no optiscaler install with id={args.install_id}", file=sys.stderr)
        sys.exit(1)
    try:
        release = _find_release(m, args.version, source_key=row["source_key"]) if args.version else None
        result = m.update_optiscaler(args.install_id, release=release)
    except (ValueError, OptiScalerError, ExtractionError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Updated -> {result.target_dir}")


def cmd_optiscaler_uninstall(args, m: Manager) -> None:
    try:
        m.uninstall_optiscaler(args.install_id)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Uninstalled OptiScaler install #{args.install_id}.")


def cmd_gui(args, m: Manager) -> None:
    m.close()
    from .gui.app import run_gui

    run_gui()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dlss-manager", description="Scan Steam games and manage DLSS DLL versions.")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="scan installed Steam games for DLSS components")
    s.add_argument("--no-collect", action="store_true", help="don't copy found DLLs into the local library")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("list", help="list scanned games and their DLSS components")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("library", help="list DLL versions in the local library")
    s.add_argument("--component", choices=[c.key for c in COMPONENTS], default=None)
    s.set_defaults(func=cmd_library)

    s = sub.add_parser("import", help="import a DLL file into the local library")
    s.add_argument("path")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("apply", help="swap a game's DLSS component to a library version")
    s.add_argument("app_id")
    s.add_argument("component", choices=[c.key for c in COMPONENTS])
    s.add_argument("library_id", type=int)
    s.set_defaults(func=cmd_apply)

    s = sub.add_parser("history", help="show applied (non-rolled-back) changes")
    s.add_argument("--app-id", default=None)
    s.set_defaults(func=cmd_history)

    s = sub.add_parser("rollback", help="undo a single applied change")
    s.add_argument("change_id", type=int)
    s.set_defaults(func=cmd_rollback)

    s = sub.add_parser("rollback-all", help="undo every change this app has ever applied")
    s.set_defaults(func=cmd_rollback_all)

    s = sub.add_parser("clean-logs", help="find/remove known DLSS-wrapper log & state files")
    s.add_argument("--app-id", default=None, help="limit to one game")
    s.add_argument("--apply", action="store_true", help="actually delete (default is dry-run)")
    s.set_defaults(func=cmd_clean_logs)

    s = sub.add_parser("clean-library", help="remove orphaned/stale files from the local DLL library")
    s.set_defaults(func=cmd_clean_library)

    s = sub.add_parser("optiscaler-sources", help="list available OptiScaler sources (official + forks)")
    s.set_defaults(func=cmd_optiscaler_sources)

    s = sub.add_parser("optiscaler-releases", help="list recent OptiScaler releases from GitHub")
    s.add_argument("--source", default="official", help="source key, see optiscaler-sources (default: official)")
    s.add_argument("--limit", type=int, default=15)
    s.set_defaults(func=cmd_optiscaler_releases)

    s = sub.add_parser("optiscaler-targets", help="suggest install folders for a game")
    s.add_argument("app_id")
    s.set_defaults(func=cmd_optiscaler_targets)

    s = sub.add_parser("optiscaler-install", help="download and install OptiScaler into a game")
    s.add_argument("app_id")
    s.add_argument("--source", default="official", help="source key, see optiscaler-sources (default: official)")
    s.add_argument("--target", default=None, help="folder to install into (default: best guess)")
    s.add_argument("--proxy", choices=PROXY_DLL_CHOICES, default="dxgi.dll")
    s.add_argument("--version", default=None, help="release tag (default: latest)")
    s.add_argument("--overwrite", action="store_true", help="overwrite an existing file with the same proxy name")
    s.set_defaults(func=cmd_optiscaler_install)

    s = sub.add_parser("optiscaler-list", help="list active OptiScaler installs")
    s.set_defaults(func=cmd_optiscaler_list)

    s = sub.add_parser("optiscaler-check", help="check installed OptiScaler versions against the latest release")
    s.set_defaults(func=cmd_optiscaler_check)

    s = sub.add_parser("optiscaler-update", help="update an install to a release (default: latest)")
    s.add_argument("install_id", type=int)
    s.add_argument("--version", default=None)
    s.set_defaults(func=cmd_optiscaler_update)

    s = sub.add_parser("optiscaler-uninstall", help="remove an OptiScaler install")
    s.add_argument("install_id", type=int)
    s.set_defaults(func=cmd_optiscaler_uninstall)

    s = sub.add_parser("gui", help="launch the graphical interface")
    s.set_defaults(func=cmd_gui)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    m = Manager()
    try:
        args.func(args, m)
    finally:
        m.close()
