"""Framework-neutral typed DAG compiler and executor for RiboTome."""
from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping


class GraphError(RuntimeError):
    """Base class for graph declaration, planning, and execution failures."""


class GraphValidationError(GraphError):
    pass


class PlanError(GraphError):
    pass


class NodeExecutionError(GraphError):
    pass


_TYPE_CHECKERS: dict[str, Callable[[Any], bool]] = {
    "any": lambda value: True,
    "str": lambda value: isinstance(value, str),
    "int": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "bool": lambda value: isinstance(value, bool),
    "json": lambda value: isinstance(value, (dict, list)),
    "dict": lambda value: isinstance(value, dict),
    "list": lambda value: isinstance(value, list),
    "path": lambda value: isinstance(value, (str, Path)),
}


@dataclass(frozen=True)
class Port:
    type: str = "any"
    required: bool = True
    description: str = ""
    source: str | None = None

    def validate(self, value: Any, *, label: str) -> None:
        if value is None:
            if self.required:
                raise NodeExecutionError(f"{label} is required but null")
            return
        checker = _TYPE_CHECKERS.get(self.type)
        if checker is None:
            raise GraphValidationError(f"{label} declares unknown type {self.type!r}")
        if not checker(value):
            raise NodeExecutionError(
                f"{label} expected {self.type}, got {type(value).__name__}"
            )


NodeRunner = Callable[[Mapping[str, Any]], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]


@dataclass(frozen=True)
class Node:
    id: str
    inputs: Mapping[str, Port] = field(default_factory=dict)
    outputs: Mapping[str, Port] = field(default_factory=dict)
    depends: tuple[str, ...] = ()
    run: NodeRunner | None = None
    human: bool = False
    decision: bool = False
    decision_inputs: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class ExecutionPlan:
    start: str
    end: str
    node_ids: tuple[str, ...]
    waves: tuple[tuple[str, ...], ...]
    required_inputs: Mapping[str, Port]
    optional_inputs: Mapping[str, Port]
    outputs: Mapping[str, Port]
    decision_inputs: Mapping[str, Port] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        def ports(values: Mapping[str, Port]) -> dict[str, Any]:
            return {
                name: {
                    "type": port.type,
                    "required": port.required,
                    "description": port.description,
                    **({"source": port.source} if port.source else {}),
                }
                for name, port in values.items()
            }
        return {
            "from": self.start,
            "to": self.end,
            "nodes": list(self.node_ids),
            "waves": [list(wave) for wave in self.waves],
            "required_inputs": ports(self.required_inputs),
            "optional_inputs": ports(self.optional_inputs),
            "outputs": ports(self.outputs),
            "preconfigurable_decisions": ports(self.decision_inputs),
        }


@dataclass
class RunResult:
    plan: ExecutionPlan
    values: dict[str, Any]
    node_outputs: dict[str, dict[str, Any]]

    def serializable(self) -> dict[str, Any]:
        return {
            "plan": self.plan.as_dict(),
            "outputs": {
                name: self.values.get(name) for name in self.plan.outputs
            },
            "node_outputs": self.node_outputs,
        }


class Graph:
    """A deep module for declaration validation, range planning, and execution."""

    def __init__(self, nodes: list[Node]):
        self.nodes = {node.id: node for node in nodes}
        if len(self.nodes) != len(nodes):
            raise GraphValidationError("duplicate node id")
        self._order = tuple(node.id for node in nodes)
        self._producers: dict[str, str] = {}
        self.validate()

    def validate(self) -> None:
        errors: list[str] = []
        producers: dict[str, str] = {}
        for node in self.nodes.values():
            if not node.id:
                errors.append("node has empty id")
            unknown = [dep for dep in node.depends if dep not in self.nodes]
            if unknown:
                errors.append(f"node {node.id!r} has unknown dependencies {unknown}")
            if not node.human and node.run is None:
                errors.append(f"node {node.id!r} has no runner")
            unknown_decisions = set(node.decision_inputs) - set(node.inputs)
            if unknown_decisions:
                errors.append(
                    f"node {node.id!r} marks unknown decision inputs {sorted(unknown_decisions)}"
                )
            if node.decision_inputs and not node.decision:
                errors.append(
                    f"node {node.id!r} declares decision_inputs but decision is false"
                )
            if node.run is not None:
                signature = inspect.signature(node.run)
                params = list(signature.parameters.values())
                if len(params) != 1 or params[0].kind not in {
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                }:
                    errors.append(
                        f"node {node.id!r} runner must accept exactly one input mapping"
                    )
            for name, port in {**node.inputs, **node.outputs}.items():
                if port.type not in _TYPE_CHECKERS:
                    errors.append(
                        f"node {node.id!r} port {name!r} has unknown type {port.type!r}"
                    )
            for name in node.outputs:
                previous = producers.get(name)
                if previous:
                    errors.append(
                        f"output {name!r} has multiple producers: {previous!r}, {node.id!r}"
                    )
                producers[name] = node.id

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                errors.append(f"cycle detected at node {node_id!r}")
                return
            if node_id in visited:
                return
            visiting.add(node_id)
            for dep in self.nodes[node_id].depends:
                visit(dep)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in self._order:
            visit(node_id)

        for node in self.nodes.values():
            ancestors = self.ancestors(node.id) if not errors else set(node.depends)
            for name in node.inputs:
                producer = producers.get(name)
                if producer and producer not in ancestors:
                    errors.append(
                        f"node {node.id!r} consumes {name!r} from non-upstream "
                        f"producer {producer!r}"
                    )
        consumed = {
            name for node in self.nodes.values() for name in node.inputs
        }
        for node in self.nodes.values():
            # Outputs of a leaf are legitimate graph results. Outputs from an
            # internal node must be connected; otherwise the declaration drops
            # a value while pretending the graph preserves it.
            if self.descendants(node.id):
                for name in node.outputs:
                    if name not in consumed:
                        errors.append(
                            f"internal node {node.id!r} produces unconsumed output {name!r}"
                        )
        if errors:
            raise GraphValidationError("Graph validation failed:\n- " + "\n- ".join(errors))
        self._producers = producers

    def ancestors(self, node_id: str) -> set[str]:
        if node_id not in self.nodes:
            raise PlanError(f"unknown node {node_id!r}")
        found: set[str] = set()
        queue = list(self.nodes[node_id].depends)
        while queue:
            current = queue.pop()
            if current in found:
                continue
            found.add(current)
            queue.extend(self.nodes[current].depends)
        return found

    def descendants(self, node_id: str) -> set[str]:
        if node_id not in self.nodes:
            raise PlanError(f"unknown node {node_id!r}")
        found: set[str] = set()
        changed = True
        while changed:
            changed = False
            for candidate in self.nodes.values():
                if candidate.id in found or candidate.id == node_id:
                    continue
                if any(dep == node_id or dep in found for dep in candidate.depends):
                    found.add(candidate.id)
                    changed = True
        return found

    def plan(self, start: str, end: str) -> ExecutionPlan:
        if start not in self.nodes or end not in self.nodes:
            missing = [name for name in (start, end) if name not in self.nodes]
            raise PlanError(f"unknown range endpoint(s): {missing}")
        if start != end and start not in self.ancestors(end):
            raise PlanError(f"node {start!r} is not upstream of {end!r}")

        allowed = ({start} | self.descendants(start)) & ({end} | self.ancestors(end))
        # Include side ancestors required by nodes in the bounded path, but stop at
        # external values rather than executing nodes that precede the chosen start.
        changed = True
        while changed:
            changed = False
            for node_id in tuple(allowed):
                for dep in self.nodes[node_id].depends:
                    if dep in self.ancestors(start) and dep != start:
                        continue
                    if dep not in allowed:
                        allowed.add(dep)
                        changed = True

        ordered = tuple(node_id for node_id in self._order if node_id in allowed)
        produced_inside = {
            name
            for node_id in ordered
            for name in self.nodes[node_id].outputs
        }
        required: dict[str, Port] = {}
        optional: dict[str, Port] = {}
        for node_id in ordered:
            for name, port in self.nodes[node_id].inputs.items():
                producer = self._producers.get(name)
                if producer in allowed:
                    continue
                target = required if port.required else optional
                existing = target.get(name)
                if existing and existing.type != port.type:
                    raise PlanError(
                        f"external input {name!r} has incompatible types "
                        f"{existing.type!r} and {port.type!r}"
                    )
                target[name] = port
                if port.required:
                    optional.pop(name, None)
                elif name in required:
                    optional.pop(name, None)

        waves: list[tuple[str, ...]] = []
        remaining = list(ordered)
        done: set[str] = set()
        while remaining:
            wave = tuple(
                node_id for node_id in remaining
                if all(dep not in allowed or dep in done for dep in self.nodes[node_id].depends)
            )
            if not wave:
                raise PlanError("selected range cannot be topologically ordered")
            waves.append(wave)
            done.update(wave)
            remaining = [node_id for node_id in remaining if node_id not in wave]

        terminal_outputs = dict(self.nodes[end].outputs)
        decision_inputs = {
            name: port
            for node_id in ordered
            if self.nodes[node_id].decision
            for name, port in self.nodes[node_id].inputs.items()
            if name in self.nodes[node_id].decision_inputs
            if self._producers.get(name) not in allowed
        }
        return ExecutionPlan(
            start=start,
            end=end,
            node_ids=ordered,
            waves=tuple(waves),
            required_inputs=required,
            optional_inputs=optional,
            outputs=terminal_outputs,
            decision_inputs=decision_inputs,
        )

    async def run_async(
        self, plan: ExecutionPlan, inputs: Mapping[str, Any]
    ) -> RunResult:
        graph_inputs = {
            name for node in self.nodes.values() for name in node.inputs
        }
        unknown = set(inputs) - graph_inputs
        if unknown:
            raise NodeExecutionError(f"undeclared external inputs: {sorted(unknown)}")
        values = dict(inputs)
        for name, port in plan.required_inputs.items():
            if name not in values:
                raise NodeExecutionError(f"missing required external input {name!r}")
            port.validate(values[name], label=f"external input {name!r}")
        for name, port in plan.optional_inputs.items():
            if name in values:
                port.validate(values[name], label=f"external input {name!r}")

        node_outputs: dict[str, dict[str, Any]] = {}
        for wave in plan.waves:
            async def execute(node_id: str) -> tuple[str, dict[str, Any]]:
                node = self.nodes[node_id]
                if node.human:
                    raise NodeExecutionError(
                        f"human node {node_id!r} requires an explicit supplied adapter"
                    )
                node_input: dict[str, Any] = {}
                for name, port in node.inputs.items():
                    value = values.get(name)
                    port.validate(value, label=f"node {node_id!r} input {name!r}")
                    if value is not None or name in values:
                        node_input[name] = value
                result = node.run(node_input)  # type: ignore[misc]
                if inspect.isawaitable(result):
                    result = await result
                if not isinstance(result, Mapping):
                    raise NodeExecutionError(
                        f"node {node_id!r} returned {type(result).__name__}, expected mapping"
                    )
                actual = set(result)
                expected = set(node.outputs)
                if actual != expected:
                    raise NodeExecutionError(
                        f"node {node_id!r} output mismatch; missing={sorted(expected-actual)}, "
                        f"undeclared={sorted(actual-expected)}"
                    )
                output = dict(result)
                for name, port in node.outputs.items():
                    port.validate(output.get(name), label=f"node {node_id!r} output {name!r}")
                return node_id, output

            results = await asyncio.gather(*(execute(node_id) for node_id in wave))
            for node_id, output in results:
                collision = set(output) & set(values)
                if collision:
                    raise NodeExecutionError(
                        f"node {node_id!r} attempted to overwrite values {sorted(collision)}"
                    )
                values.update(output)
                node_outputs[node_id] = output
        return RunResult(plan=plan, values=values, node_outputs=node_outputs)

    def run(self, plan: ExecutionPlan, inputs: Mapping[str, Any]) -> RunResult:
        return asyncio.run(self.run_async(plan, inputs))

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "id": node.id,
                "depends": list(node.depends),
                "human": node.human,
                "decision": node.decision,
                "decision_inputs": list(node.decision_inputs),
                "description": node.description,
                "inputs": {
                    name: {"type": port.type, "required": port.required, "description": port.description}
                    for name, port in node.inputs.items()
                },
                "outputs": {
                    name: {"type": port.type, "required": port.required, "description": port.description}
                    for name, port in node.outputs.items()
                },
            }
            for node in self.nodes.values()
        ]


def dump_json(value: Any, path: str | Path | None = None) -> str:
    text = json.dumps(value, indent=2, default=str)
    if path:
        Path(path).write_text(text + "\n", encoding="utf-8")
    return text
