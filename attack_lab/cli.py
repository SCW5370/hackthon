"""AttackLab CLI — list / show / run / run-all 命令."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .loader import AttackCase
from .runner import run_replay, run_all_replay


def cmd_list(args: argparse.Namespace) -> None:
    """列出所有 AttackCase."""
    cases = AttackCase.list_cases()
    if not cases:
        print("No attack cases found.")
        return
    print(f"{'attack_id':<35} {'title'}")
    print("-" * 80)
    for c in cases:
        print(f"{c.attack_id:<35} {c.title}")


def cmd_show(args: argparse.Namespace) -> None:
    """显示单个 AttackCase 详情."""
    case = AttackCase.find(args.attack_id)
    if case is None:
        print(f"ERROR: attack case '{args.attack_id}' not found.", file=sys.stderr)
        sys.exit(1)
    print(f"=== {case.attack_id} ===")
    print(f"Title:       {case.title}")
    print(f"Channel:     {case.channel}")
    print(f"Task:        {case.operator_task}")
    print(f"Untrusted:   {case.untrusted_content or '(none)'}")
    print(f"Actor:       {case.actor_claims}")
    print(f"Intent:      {case.expected_agent_intent}")
    print(f"SafeExec:    {case.expected_safeexec}")
    print(f"Recovery:    {case.expected_recovery}")


def cmd_run(args: argparse.Namespace) -> None:
    """运行单个攻击用例 replay."""
    case = AttackCase.find(args.attack_id)
    if case is None:
        print(f"ERROR: attack case '{args.attack_id}' not found.", file=sys.stderr)
        sys.exit(1)

    result, _ = run_replay(case)

    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(result.to_markdown())

    # Exit code: 0 = blocked/normal, 1 = attack succeeded or error
    sys.exit(0 if result.verdict in ("attack_blocked", "normal_allowed") else 1)


def cmd_run_all(args: argparse.Namespace) -> None:
    """运行全部攻击用例 replay."""
    cases = AttackCase.list_cases()
    if not cases:
        print("No attack cases found.", file=sys.stderr)
        sys.exit(1)

    results = run_all_replay(cases)

    if args.format == "json":
        output = [r.to_dict() for r in results]
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print("# AttackLab — All Cases Report\n")
        blocked = 0
        succeeded = 0
        for r in results:
            print(r.to_markdown())
            print()
            if r.verdict == "attack_blocked":
                blocked += 1
            elif r.verdict == "attack_succeeded":
                succeeded += 1
        print("---")
        print(f"Total: {len(results)} | 🛡️  blocked: {blocked} | ⚠️  succeeded: {succeeded}")

    sys.exit(0 if succeeded == 0 else 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="attack_lab", description="SafeExec AttackLab")
    sub = parser.add_subparsers(dest="command")

    # list
    p_list = sub.add_parser("list", help="列出所有攻击用例")

    # show
    p_show = sub.add_parser("show", help="显示用例详情")
    p_show.add_argument("attack_id", help="攻击用例 ID")

    # run
    p_run = sub.add_parser("run", help="运行单个攻击用例 (replay 模式)")
    p_run.add_argument("attack_id", help="攻击用例 ID")
    p_run.add_argument(
        "--target",
        default="replay",
        choices=["replay", "orchestrator_http", "runtime_http"],
        help="执行目标 (默认 replay)",
    )
    p_run.add_argument(
        "--format",
        default="markdown",
        choices=["markdown", "json"],
        help="输出格式 (默认 markdown)",
    )

    # run-all
    p_all = sub.add_parser("run-all", help="运行全部攻击用例")
    p_all.add_argument(
        "--target",
        default="replay",
        choices=["replay", "orchestrator_http", "runtime_http"],
        help="执行目标 (默认 replay)",
    )
    p_all.add_argument(
        "--format",
        default="markdown",
        choices=["markdown", "json"],
        help="输出格式 (默认 markdown)",
    )

    args = parser.parse_args(argv)

    if args.command == "list":
        cmd_list(args)
    elif args.command == "show":
        cmd_show(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "run-all":
        cmd_run_all(args)
    else:
        parser.print_help()
        return 1
    return 0
