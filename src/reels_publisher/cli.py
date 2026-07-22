from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .manifest import load_manifest, validate_manifest
from .pipeline import process_due_posts, retry_failures, summarize_state


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Automated Reels Publisher CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    common_manifest = argparse.ArgumentParser(add_help=False)
    common_manifest.add_argument("--manifest", type=Path, required=True)

    common_state = argparse.ArgumentParser(add_help=False)
    common_state.add_argument("--state", type=Path, required=True)

    p_validate = sub.add_parser("validate-manifest", parents=[common_manifest])
    p_validate.add_argument("--repo-root", type=Path, default=Path.cwd())

    p_post_due = sub.add_parser("post-due", parents=[common_manifest, common_state])
    p_post_due.add_argument("--repo-root", type=Path, default=Path.cwd())
    p_post_due.add_argument("--dry-run", action="store_true")
    p_post_due.add_argument("--row-number", type=int, help="Only process one manifest row number, including the header row.")
    p_post_due.add_argument("--retry-failed", action="store_true", help="Retry platforms with previous failed attempts.")

    sub.add_parser("retry-failures", parents=[common_state])
    sub.add_parser("report", parents=[common_state])

    return parser


def run(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate-manifest":
        rows = load_manifest(args.manifest)
        errors = validate_manifest(rows, args.repo_root)
        if errors:
            print("Manifest validation failed:")
            for err in errors:
                print(f"- {err}")
            return 1
        print(f"Manifest is valid ({len(rows)} rows).")
        return 0

    if args.command == "post-due":
        _load_env_file(args.repo_root / ".env")
        result = process_due_posts(
            manifest_path=args.manifest,
            state_path=args.state,
            repo_root=args.repo_root,
            dry_run=args.dry_run,
            row_number=args.row_number,
            retry_failed=args.retry_failed,
        )
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if args.command == "retry-failures":
        result = retry_failures(args.state)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if args.command == "report":
        result = summarize_state(args.state)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    parser.print_help()
    return 1
