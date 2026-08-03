"""Pitch Synthase node declarations and adapters for the RiboTome graph."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Mapping

_HERE = Path(__file__).parent
_INTELLURIC_ENV = Path("/home/director/intelluric/local/intelluric/intelluric-site.env")
if _INTELLURIC_ENV.exists():
    for _line in _INTELLURIC_ENV.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _, _value = _line.partition("=")
            os.environ.setdefault(_key.strip(), _value.strip())
# Runtime state belongs to the checkout invoking this adapter. Do not inherit an
# INSTANT_* path from the shared secrets env or another worktree.
os.environ["INSTANT_DB_PATH"] = str(_HERE / "local_state" / "workshop.db")
os.environ["INSTANT_JOBS_DIR"] = str(_HERE / "local_state" / "jobs")

import db
import prompts
import workers
from ribotome_graph import Graph, Node, Port


def _selected_template(selected_approach: Mapping[str, Any]) -> dict[str, Any]:
    archetype_id = str(selected_approach.get("effective_archetype_id") or "")
    archetype = prompts.archetype_by_id(archetype_id) or {}
    return {
        "candidate_id": str(selected_approach.get("approach_id") or "selected"),
        "focus_id": "",
        "style_label": str(selected_approach.get("label") or ""),
        "style_tags": [],
        "effective_archetype_id": archetype_id,
        "effective_archetype": archetype,
        "archetype_a": selected_approach.get("archetype_a") or {},
        "archetype_b": selected_approach.get("archetype_b") or {},
        "pitch_angle": str(selected_approach.get("pitch_angle") or ""),
        "key_differentiator": str(selected_approach.get("key_differentiator") or ""),
        "visual_direction": str(selected_approach.get("visual_direction") or ""),
        "vibe_semantics": selected_approach.get("vibe_semantics") or {},
    }


def _prepare_pitch(values: Mapping[str, Any]) -> Mapping[str, Any]:
    db.init_db()
    association_words = list(values["association_words"])
    if len(association_words) != 3 or any(not str(word).strip() for word in association_words):
        raise ValueError("association_words must contain exactly three non-empty values")
    job_id, _ = db.create_job("", str(values["audience"]), association_words)
    aspect_modes = values.get("pitch_aspect_modes") or {
        category: "INFER" for category in prompts.PITCH_ASPECT_CATEGORIES
    }
    excepted = values.get("excepted_inference_elements") or ["pricing"]
    db.update_job(
        job_id,
        elevator_pitch=str(values["elevator_pitch"]),
        conveys=str(values.get("conveys") or ""),
        doc_text=str(values.get("doc_text") or "") or None,
        association_words=association_words,
        selected_archetype_id=str(values.get("archetype_id") or "") or None,
        pitch_aspect_modes=aspect_modes,
        excepted_inference_elements=excepted,
        inferred_element_decisions={key: False for key in excepted},
        infer_prototype=0,
        explicit_slide_count=(int(values["slide_count"]) - 2) if values.get("slide_count") else None,
    )
    user_inputs = {
        "page_1": {
            "audience": str(values["audience"]),
            "conveys": str(values.get("conveys") or ""),
        },
        "page_2": {
            "elevator_pitch": str(values["elevator_pitch"]),
            "supporting_document_provided": bool(values.get("doc_text")),
            "supporting_image": False,
            "optional_notes": None,
        },
    }
    strategy_dir = workers.job_dir(job_id) / "strategy"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    supporting_paths: list[str] = []
    for raw_path in values.get("supporting_image_paths") or []:
        source = Path(str(raw_path)).expanduser().resolve()
        if not source.is_file():
            raise ValueError(f"supporting image does not exist: {source}")
        intake_dir = workers.job_dir(job_id) / "intake"
        intake_dir.mkdir(parents=True, exist_ok=True)
        destination = intake_dir / source.name
        shutil.copy2(source, destination)
        supporting_paths.append(str(destination))
    if supporting_paths:
        user_inputs["page_2"]["supporting_image"] = True
    return {
        "job_id": job_id,
        "user_inputs": user_inputs,
        "strategy_dir": str(strategy_dir),
        "source_packet": {
            "audience": str(values["audience"]),
            "elevator_pitch": str(values["elevator_pitch"]),
            "conveys": str(values.get("conveys") or ""),
            "doc_text": str(values.get("doc_text") or ""),
        },
        "copied_image_paths": supporting_paths,
    }


async def _draft_approaches(values: Mapping[str, Any]) -> Mapping[str, Any]:
    await workers.approach_drafter_worker(str(values["job_id"]))
    job = db.get_job(str(values["job_id"])) or {}
    candidates = job.get("approach_candidates_json") or []
    if not candidates:
        raise RuntimeError(job.get("error_message") or "approach drafter returned no candidates")
    return {"approach_candidates": list(candidates)}


async def _render_approach_previews(values: Mapping[str, Any]) -> Mapping[str, Any]:
    job_id = str(values["job_id"])
    candidates = list(values["approach_candidates"])
    await workers._chunked_gather([
        workers.single_slide_preview_worker(job_id, str(candidate["approach_id"]))
        for candidate in candidates
    ], chunk_size=4)
    preview_dir = workers.job_dir(job_id) / "single_slide_previews"
    previews = []
    for candidate in candidates:
        approach_id = str(candidate["approach_id"])
        matches = [
            path for suffix in (".png", ".jpg", ".jpeg", ".webp")
            if (path := preview_dir / f"{approach_id}{suffix}").is_file()
        ]
        if len(matches) != 1:
            raise RuntimeError(f"preview worker produced no image for {approach_id}")
        path = matches[0]
        previews.append({
            "approach_id": approach_id,
            "label": str(candidate.get("label") or approach_id),
            "image_path": str(path),
        })
    return {"approach_previews": previews}


def _select_approach(values: Mapping[str, Any]) -> Mapping[str, Any]:
    approach_id = str(values["selected_approach_id"])
    selected = next(
        (dict(item) for item in values["approach_candidates"]
         if str(item.get("approach_id")) == approach_id),
        None,
    )
    if not selected:
        available = [item.get("approach_id") for item in values["approach_candidates"]]
        raise ValueError(f"selected_approach_id {approach_id!r} not in {available}")
    db.update_job(
        str(values["job_id"]), selected_candidate_id=approach_id,
        selected_candidate_label=selected.get("label"),
        selected_archetype_id=selected.get("effective_archetype_id"),
        selected_archetype_label=(selected.get("effective_archetype") or {}).get("label"),
    )
    return {
        "selected_template": _selected_template(selected),
    }


def _prepare_paid(values: Mapping[str, Any]) -> Mapping[str, Any]:
    total_requested = int(values["slide_count"])
    content_requested = total_requested - 2
    content_count = prompts.paid_slide_count(explicit_slide_count=content_requested)
    total_count = prompts.deck_builder_slide_count(explicit_slide_count=content_requested)
    if total_count != total_requested:
        raise ValueError(
            f"slide_count must include opening and close; requested {total_requested}, "
            f"compiled {total_count}"
        )
    db.update_job(str(values["job_id"]), explicit_slide_count=content_count)
    return {
        "content_slide_count": content_count,
        "total_slide_count": total_count,
    }


async def _canonical_anchor(values: Mapping[str, Any]) -> Mapping[str, Any]:
    prompt = prompts.canonical_anchor_writer_prompt(
        mode="PAID",
        user_inputs=values["user_inputs"],
        selected_template=values["selected_template"],
        paid_slide_count_value=int(values["content_slide_count"]),
        supporting_document=(values["source_packet"] or {}).get("doc_text"),
        supporting_image=bool(values["copied_image_paths"]),
        design_philosophy=None,
    )
    response = await workers._responses_create(
        str(values["job_id"]),
        stage="ribotome_canonical_anchor",
        model=prompts.ANCHOR_MODEL,
        input=workers._anchor_input(prompt, str(values["job_id"])),
    )
    result = workers._validate_canonical_anchor_output(
        workers._parse_model_json(workers._response_text(response)),
        dict(values["user_inputs"]),
    )
    path = Path(str(values["strategy_dir"])) / "anchor_writer_output.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return {"canonical_anchor": result}


async def _founder_narrative(values: Mapping[str, Any]) -> Mapping[str, Any]:
    prompt = prompts.founder_narrative_synthesis_prompt(
        deck_generation_prompt=values["canonical_anchor"]["deck_generation_prompt"],
        user_inputs=values["user_inputs"],
        selected_template=values["selected_template"],
    )
    response = await workers._responses_create(
        str(values["job_id"]), stage="ribotome_founder_narrative",
        model=prompts.FOUNDER_NARRATIVE_MODEL,
        input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
    )
    result = workers._parse_model_json(workers._response_text(response))
    if not str(result.get("ghostwriter_system_prompt") or "").strip():
        raise RuntimeError("FNS returned no ghostwriter_system_prompt")
    (Path(str(values["strategy_dir"])) / "founder_narrative_output.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return {"founder_identity": result}


async def _visual_grammar(values: Mapping[str, Any]) -> Mapping[str, Any]:
    prompt = prompts.spirit_boarder_system_synthesis_prompt(
        deck_generation_prompt=values["canonical_anchor"]["deck_generation_prompt"],
        selected_template=values["selected_template"],
    )
    response = await workers._responses_create(
        str(values["job_id"]), stage="ribotome_visual_grammar",
        model=prompts.VISUAL_GRAMMAR_MODEL,
        input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
    )
    result = workers._parse_model_json(workers._response_text(response))
    if not str(result.get("spirit_boarder_system_prompt") or "").strip():
        raise RuntimeError("VGS returned no spirit_boarder_system_prompt")
    (Path(str(values["strategy_dir"])) / "visual_grammar_output.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return {"visual_identity": result}


async def _rhetorical_storyboard(values: Mapping[str, Any]) -> Mapping[str, Any]:
    total = int(values["total_slide_count"])
    prompt = prompts.rhetorical_deck_builder_prompt(
        deck_generation_prompt=values["canonical_anchor"]["deck_generation_prompt"],
        slide_numbers=list(range(1, total + 1)),
        total_slide_count=total,
    )
    response = await workers._responses_create(
        str(values["job_id"]), stage="ribotome_rhetorical_storyboard",
        model=prompts.DECK_DRAFTER_MODEL,
        input=workers._rhetorical_deck_builder_input(
            values["canonical_anchor"], prompt, str(values["job_id"])
        ),
    )
    result = workers._parse_model_json(workers._response_text(response))
    storyboard = result.get("rhetorical_storyboard") or []
    expected = list(range(1, total + 1))
    actual = [int(item.get("slide_number") or 0) for item in storyboard]
    if actual != expected:
        raise RuntimeError(f"rhetorical storyboard coverage mismatch: {actual} != {expected}")
    (Path(str(values["strategy_dir"])) / "rhetorical_storyboard.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return {"rhetorical_storyboard": storyboard}


async def _ghostwriter(values: Mapping[str, Any]) -> Mapping[str, Any]:
    merged = await workers._ghostwrite_storyboard_copy(
        str(values["job_id"]),
        storyboard=list(values["rhetorical_storyboard"]),
        founder_narrative_result=dict(values["founder_identity"]),
        total_slide_count=int(values["total_slide_count"]),
        strategy_dir=Path(str(values["strategy_dir"])),
        stage_prefix="ribotome",
    )
    copy = [{
        "slide_number": int(item["slide_number"]),
        "title": str(item.get("title") or ""),
        "body_points": list(item.get("body_points") or []),
    } for item in merged]
    return {"storyboard_copy": copy}


async def _spirit_boarder(values: Mapping[str, Any]) -> Mapping[str, Any]:
    copy_by_number = {
        int(item["slide_number"]): item for item in values["storyboard_copy"]
    }
    ghostwritten = [
        {**item, **copy_by_number[int(item["slide_number"])]}
        for item in values["rhetorical_storyboard"]
    ]
    completed = await workers._spirit_board_storyboard(
        str(values["job_id"]),
        rhetorical_storyboard=list(values["rhetorical_storyboard"]),
        ghostwritten_storyboard=ghostwritten,
        visual_grammar_result=dict(values["visual_identity"]),
        strategy_dir=Path(str(values["strategy_dir"])),
        stage_prefix="ribotome",
    )
    (Path(str(values["strategy_dir"])) / "ribotome_completed_storyboard.json").write_text(
        json.dumps({"storyboard": completed}, indent=2), encoding="utf-8"
    )
    return {"completed_storyboard": completed}


def _compile_image_calls(values: Mapping[str, Any]) -> Mapping[str, Any]:
    job_id = str(values["job_id"])
    storyboard = list(values["completed_storyboard"])
    strategy_dir = Path(str(values["strategy_dir"]))
    slide_specs = [{
        "slide_index": int(item["slide_number"]),
        "slide_label": f"Slide {int(item['slide_number'])}",
        "headline": str(item.get("title") or f"Slide {item['slide_number']}"),
        "body_points": [str(point).strip() for point in item.get("body_points") or [] if str(point).strip()],
        "speaker_note": str(item.get("rhetorical_job") or item.get("argument_job") or ""),
    } for item in storyboard]
    proof_plan = {
        "deck_title": "Pitch Deck",
        "mode": "PAID",
        "slide_count": int(values["total_slide_count"]),
        "requested_content_slide_count": int(values["content_slide_count"]),
        "anchor_writer_output": values["canonical_anchor"],
        "paid_storyboard": storyboard,
    }
    expected_text_map = {
        str(spec["slide_index"]): {
            "headline": spec["headline"], "body_points": spec["body_points"]
        } for spec in slide_specs
    }
    for name, payload in {
        "paid_storyboard.json": {"storyboard": storyboard},
        "deck_proof_plan.json": proof_plan,
        "slide_specs.json": slide_specs,
        "expected_text_map.json": expected_text_map,
    }.items():
        (strategy_dir / name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    reference_paths = [str(path) for path in workers._visual_reference_paths(job_id)]
    image_calls = []
    for spec in slide_specs:
        index = int(spec["slide_index"])
        item = next(row for row in storyboard if int(row["slide_number"]) == index)
        prompt = prompts.paid_slide_image_prompt(
            str(item["image_prompt"]), {}, title=spec["headline"],
            body_points=spec["body_points"],
        )
        image_calls.append({
            "slide_index": index,
            "stage": f"ribotome_render_{index}",
            "model": prompts.DECK_DRAFTER_MODEL,
            "tool": prompts.image_generation_tool(stage="paid"),
            "prompt": prompt,
            "reference_image_paths": reference_paths,
            "output_path": f"slides/slide_{index:02d}_proof.png",
        })
    (strategy_dir / "image_calls.json").write_text(
        json.dumps({"image_calls": image_calls}, indent=2), encoding="utf-8"
    )
    return {
        "slide_specs": slide_specs,
        "deck_proof_plan": proof_plan,
        "image_calls": image_calls,
    }


async def _render_slides(values: Mapping[str, Any]) -> Mapping[str, Any]:
    job_id = str(values["job_id"])
    slides_dir = workers.job_dir(job_id) / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)
    slide_specs = list(values["slide_specs"])
    proof_plan = dict(values["deck_proof_plan"])
    image_calls = list(values["image_calls"])
    expected_text_map = {
        str(spec["slide_index"]): {
            "headline": spec["headline"], "body_points": spec["body_points"]
        } for spec in slide_specs
    }
    db.update_job(
        job_id, status="rendering_slides", deck_proof_plan=proof_plan,
        slide_specs=slide_specs, expected_text_map=expected_text_map,
    )

    async def render(call: dict[str, Any]) -> tuple[int, str]:
        index = int(call["slide_index"])
        output = workers.job_dir(job_id) / str(call["output_path"])
        await workers._throttle_image_generation()
        response = await workers._responses_create(
            job_id, stage=str(call["stage"]), model=str(call["model"]),
            input=workers._render_input(str(call["prompt"]), job_id),
            tools=[dict(call["tool"])],
            output_path=output.relative_to(workers.job_dir(job_id)),
        )
        images = workers._response_images(response)
        if not images:
            raise RuntimeError(f"renderer returned no image for slide {index}")
        await workers._write_image_from_b64(images[0], output)
        return index, str(output)

    rendered = await workers._chunked_gather([render(call) for call in image_calls])
    proof_paths = [path for _, path in sorted(rendered)]
    db.update_job(
        job_id, status="awaiting_review",
        quality_results={str(spec["slide_index"]): {
            "decision": "as_generated", "layout_usable": True, "text_regions": []
        } for spec in slide_specs},
    )
    return {"proof_image_paths": proof_paths}


P = Port

PITCH_GRAPH = Graph([
    Node(
        id="prepare_pitch",
        inputs={
            "audience": P("str", description="Target audience"),
            "elevator_pitch": P("str", description="Full factual pitch"),
            "association_words": P("list", description="Exactly three rhetorical/style associations"),
            "conveys": P("str", required=False),
            "doc_text": P("str", required=False),
            "archetype_id": P("str", required=False),
            "supporting_image_paths": P("list", required=False),
            "pitch_aspect_modes": P("dict", required=False),
            "excepted_inference_elements": P("list", required=False),
            "selected_approach_id": P("str", required=False, description="Future approach choice; may be supplied before previews"),
            "slide_count": P("int", required=False, description="Total finished slides including opening and close"),
        },
        outputs={
            "job_id": P("str"), "user_inputs": P("dict"),
            "strategy_dir": P("path"), "source_packet": P("dict"),
            "copied_image_paths": P("list"),
        },
        run=_prepare_pitch,
        description="Create an isolated persisted run and normalize source inputs.",
    ),
    Node(
        id="approach_draft", depends=("prepare_pitch",),
        inputs={"job_id": P("str")},
        outputs={"approach_candidates": P("list")}, run=_draft_approaches,
        description="Draft four selectable rhetorical approaches.",
    ),
    Node(
        id="approach_previews", depends=("approach_draft",),
        inputs={"job_id": P("str"), "approach_candidates": P("list")},
        outputs={"approach_previews": P("list")},
        run=_render_approach_previews,
        description="Render one visual calibration slide for each approach.",
    ),
    Node(
        id="select_approach", depends=("approach_previews",),
        inputs={
            "job_id": P("str"), "approach_candidates": P("list"),
            "approach_previews": P("list"),
            "selected_approach_id": P("str", description="Approach ID chosen for downstream work"),
        },
        outputs={"selected_template": P("dict")},
        run=_select_approach,
        decision=True,
        decision_inputs=("selected_approach_id",),
        description="Resolve the explicit human/operator approach choice.",
    ),
    Node(
        id="prepare_paid", depends=("select_approach",),
        inputs={
            "job_id": P("str"),
            "slide_count": P("int", description="Total finished slides including opening and close"),
        },
        outputs={"content_slide_count": P("int"), "total_slide_count": P("int")},
        run=_prepare_paid,
        description="Normalize paid-deck slide counts without generating content.",
    ),
    Node(
        id="canonical_anchor", depends=("prepare_paid",),
        inputs={name: P(kind) for name, kind in {
            "job_id":"str", "user_inputs":"dict", "selected_template":"dict",
            "content_slide_count":"int", "strategy_dir":"path", "source_packet":"dict",
            "copied_image_paths":"list",
        }.items()},
        outputs={"canonical_anchor": P("dict")}, run=_canonical_anchor,
        description="Create the factual rhetorical authority.",
    ),
    Node(
        id="founder_narrative", depends=("canonical_anchor",),
        inputs={name: P(kind) for name, kind in {
            "job_id":"str", "canonical_anchor":"dict", "user_inputs":"dict",
            "selected_template":"dict", "strategy_dir":"path",
        }.items()},
        outputs={"founder_identity": P("dict")}, run=_founder_narrative,
        description="Create the Ghostwriter system prompt.",
    ),
    Node(
        id="visual_grammar", depends=("canonical_anchor",),
        inputs={name: P(kind) for name, kind in {
            "job_id":"str", "canonical_anchor":"dict", "selected_template":"dict",
            "strategy_dir":"path",
        }.items()},
        outputs={"visual_identity": P("dict")}, run=_visual_grammar,
        description="Create the Spirit Boarder system prompt.",
    ),
    Node(
        id="rhetorical_storyboard", depends=("canonical_anchor",),
        inputs={name: P(kind) for name, kind in {
            "job_id":"str", "canonical_anchor":"dict", "total_slide_count":"int",
            "strategy_dir":"path",
        }.items()},
        outputs={"rhetorical_storyboard": P("list")}, run=_rhetorical_storyboard,
        description="Organize slide rhetoric without copy or visuals.",
    ),
    Node(
        id="ghostwriter", depends=("founder_narrative", "rhetorical_storyboard"),
        inputs={name: P(kind) for name, kind in {
            "job_id":"str", "founder_identity":"dict", "rhetorical_storyboard":"list",
            "total_slide_count":"int", "strategy_dir":"path",
        }.items()},
        outputs={"storyboard_copy": P("list")}, run=_ghostwriter,
        description="Author the sole final visible copy.",
    ),
    Node(
        id="spirit_boarder", depends=("visual_grammar", "ghostwriter"),
        inputs={name: P(kind) for name, kind in {
            "job_id":"str", "visual_identity":"dict", "rhetorical_storyboard":"list",
            "storyboard_copy":"list", "strategy_dir":"path", "copied_image_paths":"list",
        }.items()},
        outputs={"completed_storyboard": P("list")}, run=_spirit_boarder,
        description="Author the final visual specifications without rewriting copy.",
    ),
    Node(
        id="compile_image_calls", depends=("spirit_boarder",),
        inputs={name: P(kind) for name, kind in {
            "job_id":"str", "completed_storyboard":"list", "strategy_dir":"path",
            "canonical_anchor":"dict", "content_slide_count":"int",
            "total_slide_count":"int",
        }.items()},
        outputs={
            "slide_specs": P("list"), "deck_proof_plan": P("dict"),
            "image_calls": P("list"),
        },
        run=_compile_image_calls,
        description="Compile inspectable per-slide image calls without generating images.",
    ),
    Node(
        id="render_slides", depends=("compile_image_calls",),
        inputs={name: P(kind) for name, kind in {
            "job_id":"str", "slide_specs":"list", "deck_proof_plan":"dict",
            "image_calls":"list",
        }.items()},
        outputs={"proof_image_paths": P("list")},
        run=_render_slides,
        description="Render every completed slide with one exact-copy authority.",
    ),
])


def graph() -> Graph:
    return PITCH_GRAPH


def runtime():
    """Return the durable control-plane runtime for this checkout."""
    from ribotome_runtime import RiboTomeRuntime
    return RiboTomeRuntime(PITCH_GRAPH, _HERE / "local_state" / "ribotome")
