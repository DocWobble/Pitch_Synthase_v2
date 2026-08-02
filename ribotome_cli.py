#!/usr/bin/env python3
"""Command-line adapter for the framework-neutral RiboTome graph."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pitch_ribotome import graph
from ribotome_graph import GraphError, dump_json


def _load_inputs(path: str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("input file must contain one JSON object")
    return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ribotome",
        description="Validate, plan, and run bounded Pitch Synthase graph ranges.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="Compile and validate all node/port declarations.")

    steps = sub.add_parser("steps", help="List addressable graph nodes and ports.")
    steps.add_argument("--json", action="store_true", dest="as_json")

    plan = sub.add_parser("plan", help="Compile a bounded range without running it.")
    plan.add_argument("--from", dest="start", required=True)
    plan.add_argument("--to", dest="end", required=True)
    plan.add_argument("--json", action="store_true", dest="as_json")

    run = sub.add_parser("run", help="Compile and execute a bounded graph range.")
    run.add_argument("--from", dest="start", required=True)
    run.add_argument("--to", dest="end", required=True)
    run.add_argument("--inputs", required=True, help="JSON object containing external inputs.")
    run.add_argument("--output", help="Write the result manifest to this JSON file.")
    run.add_argument("--plan-only", action="store_true")

    resume = sub.add_parser("resume", help="Re-run the range recorded in a prior result manifest.")
    resume.add_argument("manifest")
    resume.add_argument("--inputs", required=True, help="External inputs JSON; never copied into manifests.")
    resume.add_argument("--output")
    return parser


def _print_plan(plan, *, as_json: bool) -> None:
    if as_json:
        print(dump_json(plan.as_dict()))
        return
    print(f"Range: {plan.start} -> {plan.end}")
    for index, wave in enumerate(plan.waves, start=1):
        print(f"  wave {index}: {', '.join(wave)}")
    print("Required external inputs:")
    if not plan.required_inputs:
        print("  (none)")
    for name, port in plan.required_inputs.items():
        print(f"  {name}: {port.type} — {port.description}")
    if plan.optional_inputs:
        print("Optional external inputs:")
        for name, port in plan.optional_inputs.items():
            print(f"  {name}: {port.type} — {port.description}")
    if plan.decision_inputs:
        print("Preconfigurable decisions (supply these to run uninterrupted):")
        for name, port in plan.decision_inputs.items():
            print(f"  {name}: {port.type} — {port.description}")
    print("Range outputs:")
    for name, port in plan.outputs.items():
        print(f"  {name}: {port.type}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        pipeline = graph()
        if args.command == "validate":
            pipeline.validate()
            print(f"RiboTome graph valid: {len(pipeline.nodes)} nodes")
            return 0
        if args.command == "steps":
            description = pipeline.describe()
            if args.as_json:
                print(dump_json(description))
            else:
                for node in description:
                    deps = ", ".join(node["depends"]) or "root"
                    print(f"{node['id']}  <- {deps}")
                    print(f"  {node['description']}")
            return 0
        if args.command == "plan":
            _print_plan(pipeline.plan(args.start, args.end), as_json=args.as_json)
            return 0
        if args.command == "resume":
            manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
            args.start = manifest["plan"]["from"]
            args.end = manifest["plan"]["to"]
        plan = pipeline.plan(args.start, args.end)
        if getattr(args, "plan_only", False):
            _print_plan(plan, as_json=True)
            return 0
        inputs = _load_inputs(args.inputs)
        result = pipeline.run(plan, inputs).serializable()
        # External inputs are deliberately omitted from the persisted manifest.
        rendered = dump_json(result, getattr(args, "output", None))
        print(rendered)
        return 0
    except (GraphError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ribotome: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
