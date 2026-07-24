"""Command-line runner for the BioLab Agent."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from .contracts import ActionPlan, build_action_intent, plan_fingerprint, plan_to_dict
from .provider import OpenAICompatibleProvider
from .scenarios import SCENARIOS, get_scenario
from .transports import DryRunTransport, LegacyTransport, SafeExecTransport


def _build_transport(args: argparse.Namespace) -> Any:
    if args.transport == "dry-run":
        return DryRunTransport()
    if args.transport == "safeexec":
        return SafeExecTransport(
            os.environ.get("SAFEEXEC_RUNTIME_URL", "http://127.0.0.1:8790")
        )
    return LegacyTransport(
        bridge_url=os.environ.get("LAB_LEGACY_URL", "http://127.0.0.1:8791"),
        demo_token=os.environ.get("LAB_LEGACY_TOKEN", ""),
        confirmed_unsafe_demo=args.confirm_unsafe_demo,
    )


def _choose_plan(args: argparse.Namespace) -> tuple[ActionPlan, str]:
    scenario = get_scenario(args.scenario)
    if args.mode == "replay":
        return scenario.replay_plan, "deterministic-compromised-agent-replay"
    provider = OpenAICompatibleProvider(
        base_url=os.environ.get("LLM_BASE_URL", ""),
        api_key=os.environ.get("LLM_API_KEY", ""),
        model=os.environ.get("LLM_MODEL", ""),
    )
    return provider.plan(scenario), "live-openai-compatible-tool-call"


def run(args: argparse.Namespace) -> dict[str, Any]:
    scenario = get_scenario(args.scenario)
    plan, plan_source = _choose_plan(args)
    intent = build_action_intent(plan)
    fingerprint = plan_fingerprint(plan)
    result = _build_transport(args).submit(intent)
    return {
        "scenario": scenario.name,
        "operator_task": scenario.operator_task,
        "untrusted_record": scenario.untrusted_record,
        "plan_source": plan_source,
        "action_plan": plan_to_dict(plan),
        "plan_fingerprint": fingerprint,
        "action_intent": intent,
        "transport": args.transport,
        "execution_result": result,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--mode", choices=("replay", "llm"), default="replay")
    run_parser.add_argument(
        "--scenario", choices=tuple(SCENARIOS), default="normal"
    )
    run_parser.add_argument(
        "--transport",
        choices=("safeexec", "legacy", "dry-run"),
        default="safeexec",
    )
    run_parser.add_argument("--confirm-unsafe-demo", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command != "run":
        raise AssertionError(args.command)
    print(json.dumps(run(args), indent=2, sort_keys=True), flush=True)
    return 0
