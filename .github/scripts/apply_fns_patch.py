from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROMPTS_PATH = ROOT / "prompts.py"
WORKERS_PATH = ROOT / "workers.py"

FNS_PROMPT_BLOCK = r'''
# ─────────────────────────────────────────────────────────────────────────────
# Founder Narrative Synthesizer
# ─────────────────────────────────────────────────────────────────────────────

FOUNDER_NARRATIVE_MODEL = ANCHOR_MODEL


def founder_narrative_synthesis_prompt(
    *,
    deck_generation_prompt: str,
    user_inputs: Mapping[str, Any],
    selected_template: Mapping[str, Any],
) -> str:
    """Expand the anchor's principal founder into a verbal presentation grammar.

    The output is a prompt addendum, not slide copy. Workers concatenate it with
    the anchor narrative and visual-grammar addendum before the Deck Builder runs.
    """
    return dedent(
        f"""
        You are the Founder Narrative Synthesizer for Pitch Synthase.

        The anchor narrative below documents a presentation that has already
        happened. It establishes the pitch, the audience, the pressure in the
        room, the founding team, the governing strategic posture, and the
        argument that made the presentation successful.

        Your task is to expand one part of that account: the principal founder
        who actually gave the presentation.

        Identify the founder whose authority, motivation, and relationship to
        the problem most directly carried the pitch. Extrapolate how that
        specific person communicates when presenting this specific argument to
        this specific audience.

        Convert that person into a set of positive, observable communication
        constraints that the deck builder can apply while writing the complete
        presentation.

        This is not a copy-editing pass.

        Do not rewrite the pitch.
        Do not write slide copy.
        Do not write a storyboard.
        Do not supply example headlines.
        Do not list generic AI-writing prohibitions.
        Do not imitate a named real person, author, company, or publication.

        The objective is not to reduce the available language through bans. The
        objective is to create a distinctive response space through behaviors
        that this founder repeatedly adds to the presentation.

        Infer the founder's communication from:

        - their invented professional and personal history;
        - their relationship to the problem being solved;
        - the selected founder archetype;
        - the pitch domain and actual mechanism;
        - the audience's knowledge, incentives, vocabulary, and skepticism;
        - the transaction or decision the presentation was built to secure;
        - what this founder considers obvious, difficult, irritating,
          impressive, dangerous, or worth proving;
        - how this founder behaves when confident, challenged, explaining,
          demonstrating, conceding, or asking for commitment.

        Expand the founder as a character with a functional role in the room.
        The result must define what this person habitually DOES while speaking,
        not merely what qualities they possess.

        A usable directive changes the sentences the deck builder will produce.

        Weak:
        - authoritative
        - visionary
        - concise
        - technically credible
        - speaks with confidence

        Strong:
        - begins explanations with the physical failure that forced the design;
        - lets a decisive number stand without praising it;
        - answers objections by exposing the assumption beneath them;
        - describes market effects through the behavior of actual participants;
        - returns to one distinction the audience habitually collapses;
        - becomes more specific, rather than louder, when challenged.

        Synthesize a founder presentation grammar containing all of the
        following:

        PRINCIPAL SPEAKER

        Establish which member of the anchor's invented team carries the
        presentation and why this person, rather than another founder, is the
        natural voice of this pitch. Preserve the anchor's existing founder
        profile and extrapolate from it. Do not replace the founder with a new
        character.

        ROLE IN THE ROOM

        Define the founder's relationship to the audience and the precise
        conversational job they are performing: demonstrating, recruiting,
        correcting, translating, challenging, de-risking, negotiating,
        teaching, or another role forced by this pitch.

        GUIDING BEHAVIORS

        Produce six to ten positive behavioral directives. Each must describe a
        recurring action the founder takes while constructing an argument.

        The behaviors must cover:

        - how the founder opens a subject;
        - how they explain the product or mechanism;
        - how they establish evidence;
        - how they discuss customers, markets, or institutions;
        - how they handle objections and uncertainty;
        - how they use numbers;
        - how they distinguish this proposal from alternatives;
        - how they make the final ask.

        SPEECH PATTERNS

        Produce four to eight repeatable sentence-level tendencies governing
        cadence, syntax, vocabulary, emphasis, transitions, comparison, and
        compression.

        These are not catchphrases or fixed sentence templates. They are
        generative habits capable of producing many different sentences while
        keeping the same speaker recognizable.

        DOMAIN LANGUAGE

        Identify the kinds of concrete nouns, verbs, distinctions, and causal
        relationships this founder naturally reaches for because of their
        background and the subject of the pitch.

        SIGNATURE BEHAVIORS

        Produce exactly three distinctive communication habits derived from
        the founder's particular history and relationship to the problem:

        - one produced by their profession or technical background;
        - one produced by what this audience persistently misunderstands;
        - one idiosyncratic but credible habit that makes the speaker
          recognizable without making the presentation comedic.

        These signature behaviors must add language or argumentative structure
        that a generic pitch writer would not independently produce.

        FUNCTIONAL RANGE

        State how the same founder's communication changes across different
        slide functions while remaining recognizably the same speaker:

        - cover or opening;
        - problem diagnosis;
        - mechanism or product explanation;
        - evidence or traction;
        - market and commercial model;
        - risk and execution;
        - transaction and final ask.

        The founder must not use one cadence for every slide. A coherent voice
        has stable motives and habits, not identical sentence construction.

        FACTUAL BOUNDARY

        The founder profile in the anchor is fictional presentation context.
        It authorizes communication behavior only.

        Do not turn invented founder biography, experience, credentials,
        employers, personal history, or room events into factual slide content.

        Do not add claims about the real company, product, team, customers,
        traction, revenue, approvals, testing, partnerships, deployments, or
        performance.

        The raw user inputs remain the factual authority. The founder narrative
        controls how those facts are expressed, selected, ordered, and
        emphasized.

        OUTPUT

        Return strict JSON only:

        {{
          "founder_narrative_addendum": "..."
        }}

        The founder_narrative_addendum must be a complete prompt block addressed
        to the deck builder.

        It must:

        - identify the principal founder by role rather than inventing a real
          identity;
        - state the founder's role in the room;
        - provide the guiding behaviors, speech patterns, domain-language
          habits, three signature behaviors, and functional range;
        - phrase every constraint as something the founder positively does;
        - explicitly govern titles, body points, diagram labels, comparisons,
          transitions, slide purposes, and the final ask;
        - tell the deck builder to write the presentation as this founder,
          rather than merely describing the founder;
        - contain enough specificity to shift the generated presentation into
          this founder's distinct verbal basin;
        - remain between 500 and 900 words;
        - contain no JSON inside the addendum;
        - contain no meta-commentary about AI, unslopping, generated writing,
          or this synthesis call.

        The addendum may use short headings and bullet points because it is an
        operational presentation-language contract. It will be concatenated
        directly after the anchor narrative and visual grammar before the deck
        builder receives the combined prompt.

        ANCHOR NARRATIVE
        ----------------
        {deck_generation_prompt}

        RAW USER INPUTS
        ---------------
        {_json(user_inputs)}

        SELECTED ARCHETYPE AND VISUAL DIRECTION
        ---------------------------------------
        {_json(selected_template)}
        """
    ).strip()


'''

WORKER_HELPER_BLOCK = r'''
async def _synthesize_anchor_expansions(
    job_id: str,
    *,
    deck_generation_prompt: str,
    user_inputs: dict,
    selected_template: dict,
    strategy_dir: Path,
    stage_prefix: str,
) -> tuple[str, dict, dict]:
    """Run visual and founder narrative synthesis from the same anchor in parallel.

    Both addenda are deterministic inputs to the Deck Builder. The original anchor
    remains first, followed by visual production constraints and then founder voice
    constraints so the verbal grammar is the final authorship instruction seen by
    the storyboard call.
    """
    visual_grammar_prompt = prompts.visual_grammar_prompt(
        deck_generation_prompt
    )
    founder_narrative_prompt = prompts.founder_narrative_synthesis_prompt(
        deck_generation_prompt=deck_generation_prompt,
        user_inputs=user_inputs,
        selected_template=selected_template,
    )

    visual_grammar_response, founder_narrative_response = await asyncio.gather(
        _responses_create(
            job_id,
            stage=f"{stage_prefix}_visual_grammar",
            model=prompts.VISUAL_GRAMMAR_MODEL,
            input=[{
                "role": "user",
                "content": [{
                    "type": "input_text",
                    "text": visual_grammar_prompt,
                }],
            }],
        ),
        _responses_create(
            job_id,
            stage=f"{stage_prefix}_founder_narrative",
            model=prompts.FOUNDER_NARRATIVE_MODEL,
            input=[{
                "role": "user",
                "content": [{
                    "type": "input_text",
                    "text": founder_narrative_prompt,
                }],
            }],
        ),
    )

    visual_grammar_result = _parse_model_json(
        _response_text(visual_grammar_response)
    )
    founder_narrative_result = _parse_model_json(
        _response_text(founder_narrative_response)
    )

    visual_grammar_addendum = str(
        visual_grammar_result.get("visual_grammar_addendum") or ""
    ).strip()
    founder_narrative_addendum = str(
        founder_narrative_result.get("founder_narrative_addendum") or ""
    ).strip()

    if not visual_grammar_addendum:
        raise RuntimeError(
            "Visual Grammar Synthesizer returned no visual_grammar_addendum"
        )
    if not founder_narrative_addendum:
        raise RuntimeError(
            "Founder Narrative Synthesizer returned no founder_narrative_addendum"
        )

    expanded_prompt = "\n\n".join((
        deck_generation_prompt.strip(),
        visual_grammar_addendum,
        founder_narrative_addendum,
    ))

    (strategy_dir / "visual_grammar_output.json").write_text(
        json.dumps(visual_grammar_result, indent=2),
        encoding="utf-8",
    )
    (strategy_dir / "founder_narrative_output.json").write_text(
        json.dumps(founder_narrative_result, indent=2),
        encoding="utf-8",
    )

    return (
        expanded_prompt,
        visual_grammar_result,
        founder_narrative_result,
    )


'''

PAID_EXPANSION_BLOCK = r'''    # Phase 1b: parallel Visual Grammar and Founder Narrative synthesis.
    # Both calls receive the untouched anchor narrative and run concurrently. Their
    # outputs are then appended in a fixed order before the Deck Builder receives the
    # prompt: anchor -> visual production grammar -> founder presentation grammar.
    try:
        (
            deck_generation_prompt,
            visual_grammar_result,
            founder_narrative_result,
        ) = await _synthesize_anchor_expansions(
            job_id,
            deck_generation_prompt=deck_generation_prompt,
            user_inputs=user_inputs,
            selected_template=selected_template,
            strategy_dir=strategy_dir,
            stage_prefix="paid",
        )

        anchor_json["deck_generation_prompt"] = deck_generation_prompt
        db.append_progress(
            job_id,
            "generation",
            "Visual and founder presentation grammars synthesized",
            pct=0.18,
        )
    except Exception as e:
        append_telemetry(
            job_id,
            {
                "event_type": "workflow",
                "stage": "paid_anchor_expansion",
                "status": "failed",
                "error": str(e)[:500],
            },
        )
        db.update_job(
            job_id,
            status="failed",
            error_message=f"Anchor expansion synthesis failed: {e}",
        )
        return

    # Phase 2: Deck builder storyboard — one text call that plans all N slides with
'''


def patch_prompts() -> None:
    text = PROMPTS_PATH.read_text(encoding="utf-8")
    if "def founder_narrative_synthesis_prompt(" in text:
        return

    marker = (
        "# ─────────────────────────────────────────────────────────────────────────────\n"
        "# Visual Grammar Synthesizer\n"
        "# ─────────────────────────────────────────────────────────────────────────────\n"
    )
    if marker not in text:
        raise RuntimeError("prompts.py Visual Grammar marker not found")

    text = text.replace(marker, FNS_PROMPT_BLOCK + marker, 1)
    PROMPTS_PATH.write_text(text, encoding="utf-8")


def patch_workers() -> None:
    text = WORKERS_PATH.read_text(encoding="utf-8")

    if "async def _synthesize_anchor_expansions(" not in text:
        marker = (
            "# ─────────────────────────────────────────────────────────────────────────────\n"
            "# Stage 1 — Candidate preview (Procedure 1A)\n"
            "# ─────────────────────────────────────────────────────────────────────────────\n"
        )
        if marker not in text:
            raise RuntimeError("workers.py Stage 1 marker not found")
        text = text.replace(marker, WORKER_HELPER_BLOCK + marker, 1)

    pattern = re.compile(
        r"    # Phase 1b: Visual Grammar Synthesizer.*?"
        r"    # Phase 2: Deck builder storyboard — one text call that plans all N slides with\n",
        re.DOTALL,
    )
    if pattern.search(text):
        text, count = pattern.subn(PAID_EXPANSION_BLOCK, text, count=1)
        if count != 1:
            raise RuntimeError(f"Expected one VGS block replacement, got {count}")
    elif "stage=\"paid_anchor_expansion\"" not in text:
        raise RuntimeError("workers.py paid VGS block not found")

    WORKERS_PATH.write_text(text, encoding="utf-8")


def validate() -> None:
    prompts_text = PROMPTS_PATH.read_text(encoding="utf-8")
    workers_text = WORKERS_PATH.read_text(encoding="utf-8")

    ast.parse(prompts_text, filename=str(PROMPTS_PATH))
    ast.parse(workers_text, filename=str(WORKERS_PATH))

    required_prompt_fragments = (
        "FOUNDER_NARRATIVE_MODEL = ANCHOR_MODEL",
        "def founder_narrative_synthesis_prompt(",
        '"founder_narrative_addendum"',
    )
    for fragment in required_prompt_fragments:
        if fragment not in prompts_text:
            raise RuntimeError(f"Missing prompts.py fragment: {fragment}")

    required_worker_fragments = (
        "async def _synthesize_anchor_expansions(",
        "await asyncio.gather(",
        'stage=f"{stage_prefix}_visual_grammar"',
        'stage=f"{stage_prefix}_founder_narrative"',
        "visual_grammar_addendum,\n        founder_narrative_addendum,",
        'anchor_json["deck_generation_prompt"] = deck_generation_prompt',
        'stage="paid_anchor_expansion"',
    )
    for fragment in required_worker_fragments:
        if fragment not in workers_text:
            raise RuntimeError(f"Missing workers.py fragment: {fragment}")

    if workers_text.count("async def _synthesize_anchor_expansions(") != 1:
        raise RuntimeError("Anchor expansion helper must appear exactly once")
    if prompts_text.count("def founder_narrative_synthesis_prompt(") != 1:
        raise RuntimeError("FNS prompt function must appear exactly once")


if __name__ == "__main__":
    patch_prompts()
    patch_workers()
    validate()
    print("FNS integration patch applied and AST validation passed.")
