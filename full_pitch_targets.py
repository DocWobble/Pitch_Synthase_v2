#!/usr/bin/env python3
"""
Full-pitch-first validation against the 5 REAL target documents (uploaded as
part of /goal), not invented fixtures: guiclid, PetNavi, Hivemine, HigherAI,
and the SAI (semiotic AGI) white paper. Each is distilled into audience +
elevator_pitch (problem/solution) + conveys + doc_text, archetype left blank
(auto-pick) across all five for consistency. Runs approach drafting, one
regeneration test (guiclid), then 4 single-slide previews per fixture (20
images total).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).parent

_INTELLURIC_ENV = Path("/home/director/intelluric/local/intelluric/intelluric-site.env")
for _line in _INTELLURIC_ENV.read_text().splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip())

os.environ["INSTANT_DB_PATH"] = str(_HERE / "local_state" / "workshop.db")
os.environ["INSTANT_JOBS_DIR"] = str(_HERE / "local_state" / "jobs")
Path(os.environ["INSTANT_JOBS_DIR"]).mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(_HERE))
import db
import prompts
import workers

db.init_db()

FIXTURES = {
    "guiclid": dict(
        audience="developer-tools and productivity-software investors",
        elevator_pitch=(
            "Problem: conventional desktop file managers project the filesystem as storage -- "
            "folders, names, icons, timestamps -- forcing users to reconstruct what a file "
            "actually does by parsing serial text labels one at a time. Solution: guiclid "
            "replaces that projection with a deterministic geometric GUI layer derived directly "
            "from each object's real invocation behavior: files that activate become circles, "
            "containers become boxes, support/dependency files tessellate into connected "
            "honeycombs, opaque app-owned payloads become rounded triangles, and presentable "
            "content becomes a live-content surface. Geometry is never decorative -- it's a "
            "strict, ordered rule cascade (container test, activation test, runtime-boundary "
            "test, presentation test, payload test, support test, breakage test, inert test) "
            "computed from an event-driven dependency graph that updates only at file-operation "
            "boundaries, so a user can navigate and understand an entire workspace by shape and "
            "adjacency alone, even with filenames hidden."
        ),
        conveys=(
            "That this is a rigorous, deterministic system -- not a decorative 'smart desktop' "
            "skin -- and that it fundamentally changes how fast a person can understand an "
            "unfamiliar folder, project, or codebase at a glance."
        ),
        doc_text=(
            "Acceptance tests include: a directory remains navigable with filenames hidden; "
            "changing a script from read-only to executable changes its shape to a circle; a "
            "set of mutually related support files tessellates into a honeycomb; removing an "
            "owner application causes all dependent rounded triangles to become broken sharp "
            "triangles."
        ),
        archetype_id=None,
    ),
    "petnavi": dict(
        audience="developer-tools/AI-infrastructure investors and enterprise IT/security buyers",
        elevator_pitch=(
            "Problem: AI copilots and streaming avatar systems today are built ad hoc -- a "
            "language model can emit an unpredictable number of simultaneous actions per input, "
            "there's no audit trail tying an output back to the message that caused it, and "
            "swapping an ASR/LLM/TTS vendor usually means rewriting the whole pipeline. "
            "Enterprise IT also can't allow native AI copilot software on locked-down machines "
            "at all. Solution: a strict architecture (GDP) that converts every input event into "
            "exactly one Intent, one policy Beat, and one bounded ActionPlan (speech : visemes : "
            "expressions : scene : sfx capped at 1 : 1 : <=1 : <=1 : <=1), gated through a single "
            "non-bypassable Safety Sentinel that logs and can allow, rewrite, or deny any output, "
            "with every engine behind a swappable capability-flagged adapter interface. The same "
            "architecture that drives a VTuber avatar's expressions and scene changes also "
            "drives a portable desktop copilot -- and in its most locked-down form runs entirely "
            "inside a sanctioned enterprise browser via WebGPU/WASM from a USB drive, persisting "
            "all models and memory on the removable device and leaving zero residue on the host."
        ),
        conveys=(
            "That this is a disciplined, safety-first systems architecture -- not a chatbot "
            "wrapper -- and that the same rigorous core scales from a hobbyist VTuber avatar all "
            "the way to an enterprise-safe, zero-install portable AI copilot."
        ),
        doc_text=(
            "Falsification tests enforced in CI: boundary swap (voice-only vs chat-only still "
            "emits valid beats), phase denial (dropping expressions doesn't destabilize the "
            "avatar), cross-medium analogy (same RendererCmd grammar drives Live2D or VRM/Unity), "
            "10x chat-flood perturbation (bounded backlog, no starvation), and adapter swap "
            "(changing ASR/TTS/LLM vendor doesn't change Beat plans beyond capability flags)."
        ),
        archetype_id=None,
    ),
    "hivemine": dict(
        audience="crypto/DeFi and AI-infrastructure investors",
        elevator_pitch=(
            "Problem: proof-of-work cryptocurrencies burn enormous amounts of real electricity "
            "solving arbitrary puzzles that produce no useful output, while speculation and "
            "hoarding have drifted the model away from actually exchanging value. Solution: "
            "Hivemine replaces proof-of-work with proof-of-use. Idle machines ('Cells') join "
            "cooperative clusters ('Combs') and earn native tokens ('Flopchits') only for "
            "verified, useful compute cycles, validated via rotating benchmark tasks that make "
            "faking or replaying contributions infeasible. Tokens are minted when you contribute "
            "compute and spent when you draw compute back from the network, with no fiat "
            "off-ramp and no debt: a Cell with zero tokens simply can't borrow compute. Because "
            "supply expands with real compute contributed and can't be redeemed outside the "
            "network, there's no external speculative leverage -- the only return on hoarding is "
            "better access to compute later, so every idle GPU strengthens the network instead "
            "of just burning power."
        ),
        conveys=(
            "That this is fundamentally not another speculative token -- it's a closed-loop "
            "compute economy where the currency IS the useful work, and hoarding is structurally "
            "pointless."
        ),
        doc_text=(
            "Verification and safeguards: rotating benchmark pools with known solutions prevent "
            "spoofing and precomputation; only sandboxed/containerized workloads are permitted; "
            "no identity layer is required since accountability is tied to compute contribution, "
            "not user identity. Roadmap: prototype single-Comb benchmark validation, then "
            "multi-node network with real AI-inference workloads, then a fully distributed "
            "open-source compute commons."
        ),
        archetype_id=None,
    ),
    "higherai": dict(
        audience="productivity-tool and AI-workflow-software investors",
        elevator_pitch=(
            "Problem: people running structured AI workflows (candidate screening, role "
            "analysis, document review) constantly re-type the same context into prompts by "
            "hand, then manually copy the AI's response back into whatever spreadsheet or JSON "
            "file tracks the job -- there's no persistent, editable context object connecting "
            "the data, the prompt, and the AI's output. Solution: HigherAI is a workspace built "
            "around one idea -- a JSON object is the persistent context. Users load or create a "
            "JSON context file in a sidebar file tree; its fields auto-populate an editable "
            "prompt template; sending that prompt to the AI model parses the response directly "
            "back into the same JSON object; a middle panel shows the live JSON tree with inline "
            "editing and change highlights, while a bottom panel renders any linked text, "
            "images, or PDFs. Every edit -- to the JSON, the prompt, or the AI's output -- "
            "updates the same shared context object bidirectionally, so nothing has to be "
            "manually re-entered or reconciled between the data, the prompt, and the result."
        ),
        conveys=(
            "That the JSON-as-persistent-context idea is the whole product -- this isn't just a "
            "prompt UI, it's a workspace where structured data, prompts, and AI output stay in "
            "sync automatically."
        ),
        doc_text=(
            "Core logic flow: user selects/creates a JSON context object -> JSON populates UI "
            "fields (prompt template, output viewer, multimedia viewer) -> user refines the "
            "prompt (with {{placeholder}} variables pulled from JSON) -> prompt goes to the "
            "model -> response is parsed back into the same JSON object -> updated JSON informs "
            "subsequent prompts. Priorities: seamless bidirectional JSON/UI linking, "
            "template-driven prompt workflow, scalable file-tree navigation, flexible "
            "multimedia output rendering."
        ),
        archetype_id=None,
    ),
    "sai_agi": dict(
        audience="AI/ML research investors and technical due-diligence readers",
        elevator_pitch=(
            "Problem: today's large language models are built on massive subword vocabularies "
            "(50,000-100,000+ tokens) and correspondingly huge embedding layers, requiring "
            "enormous datasets and compute to learn even basic symbolic reasoning and "
            "cross-domain generalization from scratch. Solution: a semiotic, neuromimetic "
            "architecture that replaces the token vocabulary with a compact set of roughly "
            "2,000-3,000 emoji-based 'codons' as the foundational symbolic unit, combined into "
            "higher-order concepts through a symbolic attention mechanism and a recursive "
            "feedback loop that refines latent embeddings against a conceptual graph. Because "
            "meaning is carried by symbol combinations rather than huge per-token embeddings, "
            "the projected embedding layer shrinks roughly 25x (about 2 million parameters vs. "
            "about 51 million for a comparable word-token vocabulary), with corresponding "
            "projected reductions in training data, training time, and inference cost -- an "
            "optional phonetic layer maps each symbol to cross-lingual phoneme representations "
            "to generalize across languages using shared sounds rather than per-language "
            "retraining."
        ),
        conveys=(
            "That the core efficiency claim -- dramatically smaller vocabulary and embedding "
            "footprint via symbolic codons -- is the entire investment thesis, and that this "
            "could make frontier-level reasoning capability trainable on a fraction of today's "
            "compute budget."
        ),
        doc_text=(
            "Efficiency projections (self-reported architectural estimates, not yet "
            "independently benchmarked on a trained model): embedding parameters approximately "
            "2.05M vs 51.2M for a comparable word-token vocabulary (~25x reduction); projected "
            "training time reduction of 50-90%; projected training cost as low as "
            "$3,600-$10,800 in GPU-days versus a revised baseline estimate of far higher cost."
        ),
        archetype_id=None,
    ),
}


async def run_fixture(name: str, fixture: dict) -> dict:
    job_id, _ = db.create_job(problem_need="", audience=fixture["audience"], association_words=[])
    db.update_job(
        job_id,
        elevator_pitch=fixture["elevator_pitch"],
        conveys=fixture["conveys"],
        doc_text=fixture.get("doc_text"),
        selected_archetype_id=fixture.get("archetype_id"),
    )

    await workers.approach_drafter_worker(job_id)
    job = db.get_job(job_id)
    result = {"fixture": name, "job_id": job_id, "approach_status": job.get("status")}

    if job.get("status") != "approaches_ready":
        result["error"] = job.get("error_message")
        return result

    result["archetype"] = {
        "archetype_id": job["selected_archetype_id"],
        "label": job["selected_archetype_label"],
    }
    result["approaches"] = job["approach_candidates_json"]
    return result


async def run_regen_check(job_id: str) -> dict:
    result = await workers.approach_regenerate_worker(job_id, ["approach_2"])
    return {"job_id": job_id, "regenerated": result}


async def run_previews(job_id: str, approaches: list[dict]) -> dict:
    approach_ids = [a["approach_id"] for a in approaches]
    slide_results = await asyncio.gather(
        *[workers.single_slide_preview_worker(job_id, aid) for aid in approach_ids],
        return_exceptions=True,
    )
    paths, errors = {}, {}
    for aid, r in zip(approach_ids, slide_results):
        if isinstance(r, Exception):
            errors[aid] = str(r)
        else:
            paths[aid] = str(r)
    return {"job_id": job_id, "slide_paths": paths, "slide_errors": errors}


async def main():
    all_results = []
    for name, fixture in FIXTURES.items():
        print(f"\n=== Approaches: {name} ===", file=sys.stderr)
        result = await run_fixture(name, fixture)
        all_results.append(result)
        if result["approach_status"] != "approaches_ready":
            print("FAILED:", result.get("error"), file=sys.stderr)
            continue
        for a in result["approaches"]:
            print(f"  {a['approach_id']} :: {a['label']}", file=sys.stderr)

    # regeneration check on guiclid only
    guiclid_result = next(r for r in all_results if r["fixture"] == "guiclid")
    if guiclid_result["approach_status"] == "approaches_ready":
        print("\n=== Regeneration check: guiclid approach_2 ===", file=sys.stderr)
        regen = await run_regen_check(guiclid_result["job_id"])
        guiclid_result["regen_check"] = regen
        print(f"  new label: {[a['label'] for a in regen['regenerated'] if a['approach_id']=='approach_2'][0]}", file=sys.stderr)
        # keep result's approaches in sync with the regenerated set for preview stage
        guiclid_result["approaches"] = regen["regenerated"]

    for result in all_results:
        if result["approach_status"] != "approaches_ready":
            continue
        print(f"\n=== Single-slide previews: {result['fixture']} ===", file=sys.stderr)
        previews = await run_previews(result["job_id"], result["approaches"])
        result["previews"] = previews
        print(json.dumps(previews, indent=2), file=sys.stderr)

    out_path = _HERE / "local_state" / "full_pitch_targets_report.json"
    out_path.write_text(json.dumps(all_results, indent=2))
    print(f"\nSaved full report to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
