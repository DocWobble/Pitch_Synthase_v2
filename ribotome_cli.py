#!/usr/bin/env python3
"""CLI control surface for the durable RiboTome runtime."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pitch_ribotome import graph, runtime
from ribotome_graph import GraphError, dump_json
from ribotome_runtime import RuntimeError


def _load(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("input file must contain one JSON object")
    return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ribotome", description="Compile and operate durable Pitch Synthase workflow runs.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="Compile and validate the workflow definition.")
    steps = sub.add_parser("steps", help="List addressable stages and typed ports."); steps.add_argument("--json", action="store_true")
    plan = sub.add_parser("plan", help="Compile a bounded stage range."); plan.add_argument("--from", dest="start", required=True); plan.add_argument("--to", dest="end", required=True); plan.add_argument("--json", action="store_true")
    start = sub.add_parser("start", help="Create a durable run and execute until completion or suspension.")
    start.add_argument("--from", dest="start", required=True); start.add_argument("--to", dest="end", required=True); start.add_argument("--inputs"); start.add_argument("--no-execute", action="store_true")
    run = sub.add_parser("run", help="Start a new run, or advance an existing run.")
    run.add_argument("run_id", nargs="?"); run.add_argument("--from", dest="start"); run.add_argument("--to", dest="end"); run.add_argument("--inputs")
    resume = sub.add_parser("resume", help="Supply missing inputs/decisions and resume from the last accepted stage.")
    resume.add_argument("run_id"); resume.add_argument("--inputs")
    status = sub.add_parser("status", help="Inspect one durable run."); status.add_argument("run_id")
    sub.add_parser("runs", help="List durable runs.")
    events = sub.add_parser("events", help="Show the append-only event log."); events.add_argument("run_id")
    artifacts = sub.add_parser("artifacts", help="Show persisted output artifacts and hashes."); artifacts.add_argument("run_id")
    complete = sub.add_parser("complete", help="Submit typed outputs for a suspended human stage."); complete.add_argument("run_id"); complete.add_argument("--outputs", required=True)
    cancel = sub.add_parser("cancel", help="Cancel a non-terminal run."); cancel.add_argument("run_id")
    return parser


def _print_plan(plan, as_json: bool) -> None:
    if as_json:
        print(dump_json(plan.as_dict())); return
    print(f"Range: {plan.start} -> {plan.end}")
    for i, wave in enumerate(plan.waves, 1): print(f"  wave {i}: {', '.join(wave)}")
    print("Required external inputs:")
    for name, port in plan.required_inputs.items(): print(f"  {name}: {port.type} — {port.description}")
    if not plan.required_inputs: print("  (none)")
    if plan.decision_inputs:
        print("Preconfigurable decisions (supply to run uninterrupted):")
        for name, port in plan.decision_inputs.items(): print(f"  {name}: {port.type} — {port.description}")
    print("Range outputs:")
    for name, port in plan.outputs.items(): print(f"  {name}: {port.type}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        pipeline = graph()
        if args.command == "validate": pipeline.validate(); print(f"RiboTome workflow valid: {len(pipeline.nodes)} stages"); return 0
        if args.command == "steps": print(dump_json(pipeline.describe())) if args.json else [print(f"{n['id']}  <- {', '.join(n['depends']) or 'root'}\n  {n['description']}") for n in pipeline.describe()]; return 0
        if args.command == "plan": _print_plan(pipeline.plan(args.start, args.end), args.json); return 0
        engine = runtime()
        if args.command == "runs": result = engine.list()
        elif args.command == "status": result = engine.get(args.run_id)
        elif args.command == "events": result = engine.events(args.run_id)
        elif args.command == "artifacts": result = engine.artifacts(args.run_id)
        elif args.command == "complete": result = engine.complete_human(args.run_id, _load(args.outputs))
        elif args.command == "cancel": result = engine.cancel(args.run_id)
        elif args.command == "resume": result = engine.advance(args.run_id, _load(args.inputs))
        elif args.command == "start":
            result = engine.create(args.start, args.end, _load(args.inputs))
            if not args.no_execute: result = engine.advance(result["id"])
        elif args.command == "run":
            if args.run_id:
                result = engine.advance(args.run_id, _load(args.inputs))
            else:
                if not args.start or not args.end: raise ValueError("new run requires --from and --to")
                result = engine.create(args.start, args.end, _load(args.inputs)); result = engine.advance(result["id"])
        print(dump_json(result)); return 0
    except (GraphError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ribotome: {exc}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
