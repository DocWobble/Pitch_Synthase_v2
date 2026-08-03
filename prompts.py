"""
Pitch Synthase prompt and generation-parameter source of truth.

This module is PRODUCT SOURCE CODE, not agent guidance.

It intentionally preserves the product-stage boundaries from the Pitch Synthase
MVP contract:
- Template Drafter: produces four non-product-specific photographed template candidates.
- Anchor Writer: ONLY invents the artifact-pressure context and returns exactly
  {"user_inputs", "deck_generation_prompt"}.
- Deck Generator/Drafter: consumes the anchor prompt plus anchors and generates
  slide artifacts.
- Finalizer: turns presenter screenshots into textless scaffolds and assembled outputs.

No function in this module calls OpenAI. Runtime code must call the approved
OpenAI gateway/orchestrator; direct SDK calls from workers are forbidden.
"""

from __future__ import annotations

import json
import os
import re
from textwrap import dedent
from typing import Any, Literal, Mapping, Sequence, TypedDict


MAX_SLIDE_TITLE_WORDS = 10
MAX_BODY_POINTS = 3
MAX_BODY_POINT_WORDS = 12
MAX_CONTENT_VISIBLE_WORDS = 40
MAX_EDGE_VISIBLE_WORDS = 15


def _visible_word_count(value: Any) -> int:
    """Count words a viewer must read, without treating punctuation as content."""
    return len(re.findall(r"\b[\w%$]+(?:[-'][\w%$]+)*\b", str(value or "")))


def storyboard_copy_errors(
    storyboard: Sequence[Mapping[str, Any]], *, total_slide_count: int
) -> list[str]:
    """Return deterministic copy-density violations before images are rendered."""
    errors: list[str] = []
    for position, slide in enumerate(storyboard, start=1):
        try:
            slide_number = int(slide.get("slide_number") or position)
        except (TypeError, ValueError):
            slide_number = position
        title = str(slide.get("title") or "").strip()
        points = [
            str(point).strip()
            for point in (slide.get("body_points") or [])
            if str(point).strip()
        ]
        title_words = _visible_word_count(title)
        point_words = [_visible_word_count(point) for point in points]
        visible_words = title_words + sum(point_words)
        edge_slide = slide_number in {1, total_slide_count}

        if title_words > MAX_SLIDE_TITLE_WORDS:
            errors.append(
                f"Slide {slide_number} title has {title_words} words; maximum is "
                f"{MAX_SLIDE_TITLE_WORDS}."
            )
        if edge_slide:
            if len(points) > 1:
                errors.append(
                    f"Slide {slide_number} body_points has {len(points)} items; "
                    "cover and closing slides allow at most 1."
                )
            if visible_words > MAX_EDGE_VISIBLE_WORDS:
                errors.append(
                    f"Slide {slide_number} has {visible_words} visible words; cover and "
                    f"closing slides allow at most {MAX_EDGE_VISIBLE_WORDS}."
                )
            continue

        if not 1 <= len(points) <= MAX_BODY_POINTS:
            errors.append(
                f"Slide {slide_number} body_points has {len(points)} items; content "
                f"slides require 1-{MAX_BODY_POINTS}."
            )
        for point_index, word_count in enumerate(point_words, start=1):
            if word_count > MAX_BODY_POINT_WORDS:
                errors.append(
                    f"Slide {slide_number} body_points[{point_index}] has {word_count} "
                    f"words; maximum is {MAX_BODY_POINT_WORDS}."
                )
        if visible_words > MAX_CONTENT_VISIBLE_WORDS:
            errors.append(
                f"Slide {slide_number} has {visible_words} visible words; maximum is "
                f"{MAX_CONTENT_VISIBLE_WORDS}."
            )
    return errors


def storyboard_embedded_copy_errors(
    storyboard: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Reject Deck Builder image prompts that duplicate visible storyboard copy."""
    errors: list[str] = []
    for position, slide in enumerate(storyboard, start=1):
        slide_number = int(slide.get("slide_number") or position)
        image_prompt = str(slide.get("image_prompt") or "").casefold()
        visible_copy = [str(slide.get("title") or "").strip()]
        visible_copy.extend(
            str(point).strip() for point in (slide.get("body_points") or [])
        )
        for text_value in visible_copy:
            if text_value and text_value.casefold() in image_prompt:
                errors.append(
                    f"Slide {slide_number} image_prompt embeds visible storyboard "
                    f"copy: {text_value!r}."
                )
    return errors

# ---------------------------------------------------------------------------
# Model and image parameter defaults
# ---------------------------------------------------------------------------
# Runtime may override all of these. This file centralizes the values only; it
# does not authorize live API execution.

# Workshop override: the shared intelluric-site.env pins these to "-mini"
# variants (smaller context -> higher hallucination/incoherence risk across
# longer source material, e.g. a 16-slide deck or a multi-page supporting
# doc). The workshop deliberately ignores that env override and forces the
# full non-mini model for every text stage -- this constant is intentionally
# NOT read from PITCH_SYNTHASE_*_MODEL so a shared-env change can't silently
# downgrade workshop test fidelity. Does not touch the shared env file.
# gpt-5.4 is last-gen flagship: proven quality, half or less the cost of current-gen
# mini/nano tiers. Do not "upgrade" to gpt-5.6-terra/luna without a confirmed
# cost-benefit case — older flagship beats current-gen mini on value.
ANCHOR_MODEL = "gpt-5.4"
TEMPLATE_DRAFTER_MODEL = "gpt-5.4"
DECK_DRAFTER_MODEL = "gpt-5.4"
QUALITY_MODEL = os.environ.get("PITCH_SYNTHASE_QUALITY_MODEL", "gpt-5.4")
# One-word classification calls (product/brand/neither, yes/no gates). gpt-4.1-nano
# is two generations back nano — cheap and sufficient for single-token closed-set output.
CLASSIFY_MODEL = "gpt-4.1-nano"
IMAGE_MODEL = os.environ.get("PITCH_SYNTHASE_IMAGE_MODEL", "gpt-image-2")
IMAGE_SIZE = os.environ.get("PITCH_SYNTHASE_IMAGE_SIZE", "2048x1152")  # exact 16:9, gpt-image-2
IMAGE_QUALITY = os.environ.get("PITCH_SYNTHASE_IMAGE_QUALITY", "medium")
IMAGE_OUTPUT_FORMAT = os.environ.get("PITCH_SYNTHASE_IMAGE_OUTPUT_FORMAT", "png")
IMAGE_BACKGROUND = os.environ.get("PITCH_SYNTHASE_IMAGE_BACKGROUND", "opaque")

# Compatibility aliases used by older workers. Do not introduce new call sites
# that depend on the older PROOF/PLAN naming.
PLAN_MODEL = ANCHOR_MODEL
PROOF_MODEL = IMAGE_MODEL
PROOF_SIZE = IMAGE_SIZE
PROOF_QUALITY = IMAGE_QUALITY
CANDIDATE_MODEL = IMAGE_MODEL
CANDIDATE_SIZE = IMAGE_SIZE
CANDIDATE_QUALITY = IMAGE_QUALITY

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------

CANDIDATE_COUNT = 4
CANDIDATE_N = CANDIDATE_COUNT
PREVIEW_LATENT_SLIDE_COUNT = 10
PREVIEW_EXPOSED_SLIDES = (3, 6, 8)
MIN_SELF_SERVE_SLIDE_COUNT = 4
# Originally capped at 10 under the assumption slide rendering was a single
# n=10 image call. It isn't -- every slide is its own independent per-slide
# call (paid_slide_image_prompt), same as slide 1 or slide 30. The real
# constraint is throughput (_IMAGE_RATE_LIMIT calls per rolling window in
# workers.py), not slide count itself. Raised to give headroom for
# real-length decks (e.g. a 16-slide investor outline) without implying any
# new technical ceiling -- this is still a policy knob, not a hard limit.
MAX_SELF_SERVE_SLIDE_COUNT = 24

JOB_STATUSES = (
    "intake_started",
    "template_generating",
    "template_ready",
    "template_selected",
    "anchor_prompt_generating",
    "deck_generating",
    "preview_ready",
    "checkout_pending",
    "paid",
    "review_ready",
    "finalizing",
    "complete",
    "failed",
)

ANCHOR_OUTPUT_SCHEMA_KEYS = ("user_inputs", "deck_generation_prompt")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False)


# ---------------------------------------------------------------------------
# Selective non-canon exceptions
#
# The pipeline's default rule everywhere is: never invent business proof or
# facts not present in elevator_pitch/doc_text. Two mechanisms let the user
# selectively lift that restriction for specific content, without weakening
# it anywhere else:
#
#   1. excepted_inference_elements -- a user-selected list (starting set:
#      "pricing") naming which elements the user explicitly authorizes the
#      model to INFER from its own latent training, not from source material.
#      Pricing governs only invented offer pricing and related unit economics;
#      it never governs source-backed commercialization or general industry
#      economics. Authorized
#      inferences must still read as inferred proposals, not verified fact,
#      and are recorded as such (authorized_inference: true) rather than
#      silently presented as sourced.
#   2. canon_overrides -- user edits made during refinement. Once a user
#      directly edits a slide's content, that edit is authoritative going
#      forward -- it becomes new canon, equal or senior to elevator_pitch/
#      doc_text for whatever it addresses, and must not be flagged as
#      unsourced or reverted by any later verification pass.
# ---------------------------------------------------------------------------

EXCEPTED_INFERENCE_ELEMENT_CHOICES = ("pricing", "mockup_prototype_design")

PITCH_ASPECT_MODE_CHOICES = ("OMIT", "INFER", "SOURCE")
PITCH_ASPECT_CATEGORIES = {
    "customer_stakeholder_value": "customer, stakeholder, and value model",
    "market_ecosystem_competition": "market, ecosystem, and competitive context",
    "commercialization_pricing_financial": "commercialization, pricing, and financial model",
    "proof_validation_defensibility": "proof, validation, traction, and defensibility",
    "execution_team_risk": "productization, execution, roadmap, team, and risk",
    "transaction_ask_use_of_proceeds": "transaction, ask, and use of proceeds",
    "founder_biography": "founder and company biography, named team members, individual people and their roles",
}
# founder_biography defaults to SOURCE: named people/bios are falsifiable facts
# and must never be inferred from the anchor narrative or training data.
_PITCH_ASPECT_DEFAULT_OVERRIDES = {"founder_biography": "SOURCE"}
DEFAULT_PITCH_ASPECT_MODES = {
    category: _PITCH_ASPECT_DEFAULT_OVERRIDES.get(category, "INFER")
    for category in PITCH_ASPECT_CATEGORIES
}


def normalize_pitch_aspect_modes(
    pitch_aspect_modes: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Return a complete six-category mode map with INFER defaults."""
    supplied = dict(pitch_aspect_modes or {})
    unknown = sorted(set(supplied) - set(PITCH_ASPECT_CATEGORIES))
    if unknown:
        raise ValueError(f"Unknown pitch aspect categories: {unknown}")

    normalized = dict(DEFAULT_PITCH_ASPECT_MODES)
    for category, raw_mode in supplied.items():
        mode = str(raw_mode).strip().upper()
        if mode not in PITCH_ASPECT_MODE_CHOICES:
            raise ValueError(
                f"Pitch aspect {category!r} must be one of "
                f"{list(PITCH_ASPECT_MODE_CHOICES)}; got {raw_mode!r}"
            )
        normalized[category] = mode
    return normalized


def pitch_aspect_worker_briefing(
    pitch_aspect_modes: Mapping[str, Any] | None,
) -> str:
    """Build the Deck Builder's completeness mandate and SOURCE / INFER / OMIT briefing.

    The completeness mandate comes first: a successful pitch deck in this domain
    contains all standard elements by default. Absence requires explicit OMIT.
    SOURCE and INFER govern where content comes from, not whether it appears.
    """
    modes = normalize_pitch_aspect_modes(pitch_aspect_modes)
    grouped = {
        mode: [
            label
            for category, label in PITCH_ASPECT_CATEGORIES.items()
            if modes[category] == mode
        ]
        for mode in PITCH_ASPECT_MODE_CHOICES
    }

    clauses = [
        "PITCH ASPECT WORKER BRIEFING:",
        (
            "COMPLETENESS MANDATE: A successful pitch deck for this domain and audience "
            "must contain all standard elements — economics, launch plan, market framing, "
            "competitive positioning, advertising strategy, ask, and so on. The presence "
            "of each element is the default. An element may be absent only if explicitly "
            "marked OMIT below. A deck missing a key element without an OMIT flag is "
            "incomplete and fails its audience regardless of how strong the other slides are. "
            "SOURCE and INFER below govern where the content comes from, not whether it appears."
        ),
    ]
    if grouped["INFER"]:
        clauses.append(
            "INFER — produce completely, reconstruct gaps: "
            + "; ".join(grouped["INFER"])
            + ". These elements must be present and complete in the deck. Use source "
            "material wherever it speaks; fill gaps using the Inference Authority Rule "
            "wherever it is silent. The goal is a deck element that would succeed with "
            "the target audience — not a summary of what the source happened to mention. "
            "Any reconstructed concrete detail (value, number, tier, relationship) "
            "becomes deck-wide canon at first use and must be consistent everywhere."
        )
    if grouped["SOURCE"]:
        clauses.append(
            "SOURCE — produce from materials only: "
            + "; ".join(grouped["SOURCE"])
            + ". These elements must be present, sourced strictly from the provided "
            "materials. Do not reconstruct details the source does not address; include "
            "what the source gives and stop there."
        )

    always_omit = [
        *grouped["OMIT"],
        "generic fluff",
        "pointless repetition",
        "typographical errors",
    ]
    clauses.append(
        "OMIT — exclude entirely: " + "; ".join(always_omit) + ". "
        "No substantive content, claims, or examples from these aspects may appear."
    )
    return "\n\n".join(clauses)


def pitch_aspect_refinement_policy(
    pitch_aspect_modes: Mapping[str, Any] | None,
) -> str:
    """Build the verifier/judge policy without Deck Builder quality exclusions."""
    modes = normalize_pitch_aspect_modes(pitch_aspect_modes)
    grouped = {
        mode: [
            label
            for category, label in PITCH_ASPECT_CATEGORIES.items()
            if modes[category] == mode
        ]
        for mode in PITCH_ASPECT_MODE_CHOICES
    }
    clauses = [
        "PITCH ASPECT REFINEMENT POLICY:",
        "Apply these rules before the ordinary source-accuracy rules.",
    ]
    if grouped["SOURCE"]:
        clauses.append(
            "SOURCE — "
            + "; ".join(grouped["SOURCE"])
            + ": every direct claim, example, name, number, or value in these "
            "aspects must be supported by the user source or removed."
        )
    if grouped["INFER"]:
        clauses.append(
            "INFER — "
            + "; ".join(grouped["INFER"])
            + ": plausible details absent from the source are allowed. They are "
            "not errors merely because they were reconstructed. If the source "
            "addresses the same fact, the source wins. Otherwise the earliest "
            "storyboard occurrence establishes deck-wide canon, and every later "
            "use must match it exactly."
        )
    if grouped["OMIT"]:
        clauses.append(
            "OMIT — "
            + "; ".join(grouped["OMIT"])
            + ": no direct example, claim, number, named instance, diagram, or "
            "other substantive content from these aspects may appear. Any such "
            "presence is actionable and must be removed."
        )
    return "\n".join(clauses)


def _inference_policy_block(excepted_inference_elements: Sequence[str] | None) -> str:
    """Inference authority rule plus narrow guards on load-bearing specific values.

    The primary frame is the falsifiability heuristic — not a topic whitelist.
    The pricing and mockup guards protect against inventing specific committed
    values (a price point, an exact prototype design) that audience members treat
    as founder commitments and could independently call wrong. They do not suppress
    the surrounding commercialization or product content.
    """
    elements = {
        str(element).strip()
        for element in (excepted_inference_elements or [])
        if str(element).strip()
    }
    pricing_guarded = "pricing" not in elements
    mockup_guarded = "mockup_prototype_design" not in elements

    authority_block = """
    INFERENCE AUTHORITY RULE:
    Source material is the primary factual authority; it always wins when it speaks.

    Beyond the source, two categories of content are also authorized:

    Forced implications — logical, physical, and societal consequences that
    necessarily follow from stated facts. These are not inferences; they are
    completions the source requires. Carry them through specifically. An unexplained
    mechanism, an unspecified physical requirement, a safety property the design
    demands — fill these in with the specificity the pitch needs. Vagueness on a
    required implication is incompleteness, not caution.

    Gap-bridging inference — content the pitch needs that the source does not
    address, when no one in the audience could falsify the claim using knowledge
    they brought in from the world. Additional use cases the pitch didn't enumerate,
    visual angles the prototype was never shown from, unidentified people in use
    scenarios, market framing that follows from category — these have no external
    referent to contradict them.

    Forbidden: specific claims about verifiable past or present reality the model
    cannot know — real names of people or companies, historical dates, measured
    test outcomes, certifications, revenue or sales figures. These carry a non-zero
    probability of being wrong in ways an informed audience member could independently
    verify.

    The test: could someone in that room call bullshit on it based on knowledge they
    walked in with — knowledge from the world, not derived from this pitch? If yes,
    it is forbidden. If the only path to falsification runs through undisclosed
    information or testing not yet performed, it is authorized.
    """

    blocks = [authority_block]

    if pricing_guarded:
        blocks.append(
            """
            PRICING GUARD — do not invent a committed price point:
            Do not invent or state a specific retail price, subscription fee,
            licensing fee, per-unit charge, contract price, or pricing tier that
            this pitch company would charge, unless the source material states it.
            This guard exists because a specific price is a founder commitment that
            audience members treat as real and may independently know to be wrong.
            This guard does NOT suppress pricing strategy, commercialization logic,
            unit economics, revenue model, go-to-market structure, margin framing,
            or any other pricing-adjacent content. Include all of that. Only the
            specific committed figure is guarded.
            """
        )
    else:
        blocks.append(
            """
            PRICING AUTHORIZED — infer a proposed price point:
            You may reconstruct a plausible proposed price, pricing tiers, or
            unit-economics structure even when the source material is silent.
            Decide it exactly once and reuse it consistently. Present it as a
            proposal, not as a validated or existing price.
            """
        )

    if mockup_guarded:
        blocks.append(
            """
            PROTOTYPE DESIGN GUARD — do not invent a committed product design:
            Do not invent a specific committed prototype design, form factor, or
            interaction system unless source material or a provided design reference
            specifies it. A wrongly invented design is audience-visible and locks
            the founders into something they may not have built. This guard does NOT
            suppress product description, physical logic, mechanism explanation,
            safety rationale, or any other product-content adjacent to design.
            Describe what the product is and how it works; only the specific locked
            visual/physical form is guarded.
            """
        )
    else:
        blocks.append(
            """
            PROTOTYPE DESIGN AUTHORIZED — infer one proposed product design:
            You may reconstruct one plausible proposed visual and interaction system
            even when the source material is silent. Decide it exactly once and
            reuse it consistently everywhere.
            """
        )

    return "\n\n".join(dedent(block).strip() for block in blocks)


def _canon_overrides_block(canon_overrides: Mapping[str, Any] | Sequence[Any] | None) -> str:
    if not canon_overrides:
        return ""
    return dedent(
        f"""
        User-edited canon overrides -- authoritative, equal or senior to the
        source material below wherever they conflict with it. These are
        direct user edits made during a prior refinement pass, not model
        output. This changes WHICH value is correct for whatever fact each
        override addresses -- it is not a blanket exemption from checking.
        Content that matches an override is not an issue. Content that
        CONTRADICTS an override (states a different value for the same
        fact the override settled) is still an actionable issue, exactly as
        a contradiction of elevator_pitch/doc_text would be -- report the
        correction as the override's value, not the original source's.
        This applies deck-wide: an override made on one slide is canon for
        every other slide that touches the same fact, not only the slide it
        was originally edited on.
        {_json(canon_overrides)}
        """
    ).strip()


def normalize_mode(mode: str) -> str:
    mode_upper = (mode or "").upper()
    if mode_upper not in {"PREVIEW", "PAID", "CONVERT"}:
        raise ValueError(f"Unsupported Pitch Synthase mode: {mode!r}")
    return mode_upper


def paid_slide_count(explicit_slide_count: int | None = None) -> int:
    if explicit_slide_count is None:
        raise ValueError("explicit_slide_count is required for PAID/CONVERT generation")
    count = int(explicit_slide_count)
    if count < MIN_SELF_SERVE_SLIDE_COUNT or count > MAX_SELF_SERVE_SLIDE_COUNT:
        raise ValueError(
            f"Self-serve paid decks must be between {MIN_SELF_SERVE_SLIDE_COUNT} and "
            f"{MAX_SELF_SERVE_SLIDE_COUNT} slides; got {count}."
        )
    return count


def deck_builder_slide_count(explicit_slide_count: int | None = None) -> int:
    """Translate the user's requested content-slide count into the total storyboard
    count: N content slides + 1 hero cover (slide 1) + 1 thank you closing (slide N+2)."""
    return paid_slide_count(explicit_slide_count=explicit_slide_count) + 2


def _three_associations(values: Sequence[str]) -> tuple[str, str, str]:
    if len(values) != 3:
        raise ValueError("Pitch Synthase template generation requires exactly three association words/impressions.")
    a, b, c = (str(v).strip() for v in values)
    if not all((a, b, c)):
        raise ValueError("Association words/impressions cannot be blank.")
    return a, b, c


def image_request_params(*, stage: str, n: int = 1) -> dict[str, Any]:
    """
    Parameters for a direct image-generation request through the approved gateway.

    This is deliberately not a Responses tool declaration. The gateway owns the
    actual SDK call, spend checks, retry budget, request logging, and response
    persistence.
    """
    if n < 1:
        raise ValueError("image n must be >= 1")
    return {
        "stage": stage,
        "model": IMAGE_MODEL,
        "size": IMAGE_SIZE,
        "quality": IMAGE_QUALITY,
        "n": n,
        "output_format": IMAGE_OUTPUT_FORMAT,
        "background": IMAGE_BACKGROUND,
    }


def image_generation_tool(*, stage: str) -> dict[str, Any]:
    """
    Legacy compatibility for workers that still benchmark/use Responses image tools.

    New production paths should prefer image_request_params() through the approved
    OpenAI gateway. If this function is used, the caller must still enforce spend
    ceilings and must log that the Responses image-tool path was selected.
    """
    return {
        "type": "image_generation",
        "model": IMAGE_MODEL,
        "quality": IMAGE_QUALITY,
        "size": IMAGE_SIZE,
        "output_format": IMAGE_OUTPUT_FORMAT,
        "background": IMAGE_BACKGROUND,
        # Pitch Synthase consumes only the completed proof image. Keep this
        # explicit so an SDK/default change cannot begin emitting unused
        # partial generations.
        "partial_images": 0,
    }


# ---------------------------------------------------------------------------
# Stage 1 — Template Drafter
# ---------------------------------------------------------------------------

# v1 — VISUAL_FOCUSES (template_drafter_prompt_archetype_first, dead in SAWC pipeline)
# VISUAL_FOCUSES = [
#     {"focus_id": "focus_mechanism", "label": "Mechanism", ...},
#     {"focus_id": "focus_market",    "label": "Market",    ...},
#     {"focus_id": "focus_economics", "label": "Economics", ...},
#     {"focus_id": "focus_proof",     "label": "Proof",     ...},
#     {"focus_id": "focus_operations","label": "Operations",...},
#     {"focus_id": "focus_experience","label": "Experience",...},
# ]
VISUAL_FOCUSES = []  # retired; kept as empty list so template_drafter references don't hard-crash


# v1 archetypes — commented out, superseded by historically-grounded roster below
# arch_odysseus, arch_caesar, arch_davinci, arch_athena, arch_newton,
# arch_cicero, arch_copernicus, arch_gutenberg, arch_alexander, arch_michelangelo

FOUNDER_ARCHETYPES = [
    {
        "archetype_id": "arch_buffett",
        "label": "Buffett",
        "role": "The Compounding Proof Merchant",
        "posture": "Understated, evidence-led, economically literate, and patient.",
        "win_condition": (
            "This pitch wins by shifting the burden of proof onto the skeptic: it presents "
            "a trend so uninterrupted and already-observed that continuing to doubt its "
            "continuation, not believing it, becomes the position that now requires "
            "justification."
        ),
    },
    {
        "archetype_id": "arch_edison",
        "label": "Edison",
        "role": "The Commercial Demonstrator",
        "posture": "Practical, experimental, proof-oriented, and commercially relentless.",
        "win_condition": (
            "This pitch wins by proof through performance: the working demonstration "
            "itself is the argument, and a skeptic who watches it happen has no rebuttal "
            "left except to explain why what they just saw shouldn't count."
        ),
    },
    {
        "archetype_id": "arch_leonardo",
        "label": "Leonardo",
        "role": "The Patron-Oriented Synthesist",
        "posture": "Inventive, interdisciplinary, adaptive, and audience-attuned.",
        "win_condition": (
            "This pitch wins by dissolving the objection before it's raised: every "
            "component the skeptic would need to believe already exists in things they "
            "already individually accept; the argument is simply the arrangement they "
            "hadn't yet seen."
        ),
    },
    {
        "archetype_id": "arch_franklin",
        "label": "Franklin",
        "role": "The Practical System Reformer",
        "posture": "Pragmatic, first-principles-driven, civic-minded, and resourceful.",
        "win_condition": (
            "This pitch wins by making its conclusion feel self-derived: it walks the "
            "skeptic through first principles so plainly that they arrive at the idea "
            "themselves, leaving no room to resist a claim they feel they reasoned their "
            "own way into."
        ),
    },
    {
        "archetype_id": "arch_lauder",
        "label": "Lauder",
        "role": "The Demonstrative Merchant",
        "posture": "Customer-intimate, persuasive, polished, and transaction-aware.",
        "win_condition": (
            "This pitch wins by skipping the argument altogether: it puts the thing "
            "directly into the skeptic's hands under conditions where trying it costs "
            "less than doubting it, so persuasion happens through firsthand contact "
            "rather than assertion."
        ),
    },
    {
        "archetype_id": "arch_jobs",
        "label": "Jobs",
        "role": "The Category Showman",
        "posture": "Focused, theatrical, product-obsessed, and expectation-resetting.",
        "win_condition": (
            "This pitch wins by resetting the standard the skeptic is judging against: "
            "once they've seen what the category could be, defending what it used to be "
            "becomes the position that requires justification."
        ),
    },
    {
        "archetype_id": "arch_barnum",
        "label": "Barnum",
        "role": "The Attention Engineer",
        "posture": "Spectacular, publicity-minded, audience-aware, and highly transmissible.",
        "win_condition": (
            "This pitch wins by proof through contagion: the claim is made so vivid and "
            "immediately shareable that the skeptic sees everyone else already reacting "
            "to it, and skepticism starts to feel like the isolated, out-of-step position "
            "rather than the reasonable one."
        ),
    },
    {
        "archetype_id": "arch_chanel",
        "label": "Chanel",
        "role": "The Aspirational Reframer",
        "posture": "Taste-driven, culturally perceptive, identity-forming, and defiant.",
        "win_condition": (
            "This pitch wins by inverting who's on the defensive: it reframes the status "
            "quo itself as the dated, self-limiting choice, so the skeptic ends up "
            "explaining why they're still holding an old identity rather than why they "
            "should accept a new one."
        ),
    },
    {
        "archetype_id": "arch_rockefeller",
        "label": "Rockefeller",
        "role": "The Value-Chain Consolidator",
        "posture": "Disciplined, integrative, economically rigorous, and control-oriented.",
        "win_condition": (
            "This pitch wins by proof through comparison: it lays the coordinated whole "
            "next to every fragmented alternative and dares the skeptic to name one "
            "disorganized version that has matched it — the absence of a counterexample "
            "becomes the argument."
        ),
    },
    {
        "archetype_id": "arch_ford",
        "label": "Ford",
        "role": "The Production Transformer",
        "posture": "Operational, systems-minded, standardizing, and throughput-obsessed.",
        "win_condition": (
            "This pitch wins by proof the skeptic can verify themselves: the mechanics of "
            "the redesign are shown plainly enough that the audience can check the new "
            "limits with their own arithmetic rather than take them on faith — and a "
            "limit you've verified yourself is hard to keep doubting."
        ),
    },
    {
        "archetype_id": "arch_morgan",
        "label": "Morgan",
        "role": "The Capital Coordinator",
        "posture": "Institutional, coalition-oriented, financially commanding, and structural.",
        "win_condition": (
            "This pitch wins by proof through inevitability: it shows the skeptic that "
            "every party who would need to agree already has independent reason to, so "
            "accepting the pitch isn't a leap of faith — it's recognizing a structure "
            "that is already locking into place with or without them."
        ),
    },
    {
        "archetype_id": "arch_disney",
        "label": "Disney",
        "role": "The Commercial World Builder",
        "posture": "Imaginative, system-building, experience-driven, and expansion-minded.",
        "win_condition": (
            "This pitch wins by proof through immersion: rather than arguing the world "
            "could exist, it places the skeptic inside a fully realized piece of it, so "
            "doubt requires them to consciously step back out of an experience they were "
            "already having."
        ),
    },
]
for _a in FOUNDER_ARCHETYPES:
    _a["category"] = "Strategic Archetype"


def archetype_by_id(archetype_id: str) -> Mapping[str, Any] | None:
    for archetype in FOUNDER_ARCHETYPES:
        if archetype.get("archetype_id") == archetype_id:
            return archetype
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Design Philosophy Archetypes
# Used by design_drafter_worker to ensure 4 distinct prototype designs rather
# than 4 convergent elaborations of the same aesthetic. These are material,
# geometry, and aesthetic archetypes — not founder personality archetypes.
# The model selects the 4 most differentiating figures for each product run.
# ─────────────────────────────────────────────────────────────────────────────
DESIGN_ARCHETYPES = [
    {
        "archetype_id": "design_rams",
        "label": "Rams",
        "figure": "Dieter Rams",
        "reference": "1960s–80s Braun consumer electronics",
        "design_language": (
            "Reduction to the irreducible minimum. Every surface, seam, and radius must earn "
            "its presence through function. Neutral material tones (off-white, warm grey, "
            "sand) recede behind the interaction — the product should appear as if it could "
            "not be otherwise. Controls appear exactly where needed and nowhere else."
        ),
        "material_tendency": "matte ABS or similar polymer with no-flash parting lines; brushed metal accents only where structural",
        "geometric_tendency": "low-radius rectanguloid volumes; curves only where grip demands; flush surfaces; no gratuitous chamfer",
        "optimizes_for": "cognitive invisibility — the user never thinks about the object, only the task",
        "sacrifices": "visual spectacle, tactile surprise, self-narrating mechanism visibility",
    },
    {
        "archetype_id": "design_dyson",
        "label": "Dyson",
        "figure": "James Dyson",
        "reference": "1990s–2010s Dyson cyclone and blade products",
        "design_language": (
            "The mechanism IS the aesthetic. Internal working parts are architectural elements "
            "to be revealed, not hidden. Translucent or apertured housings make physics visible. "
            "Color is zoned to function: the cyclone chamber is one color, the motor housing "
            "another, the filter another. The form narrates the engineering problem it solved."
        ),
        "material_tendency": "translucent injection-molded polycarbonate over working components; visible structural ribs; vibrant accent colors assigned by function zone",
        "geometric_tendency": "complex organic-functional forms that trace internal geometry; apertures positioned to expose mechanism; color boundary = functional boundary",
        "optimizes_for": "engineering credibility — the form proves the product's claim about its own mechanism",
        "sacrifices": "material quietude, ergonomic simplicity, surface restraint",
    },
    {
        "archetype_id": "design_eames",
        "label": "Eames",
        "figure": "Charles & Ray Eames",
        "reference": "1945–70s American modernist furniture and product design",
        "design_language": (
            "Materials speak for themselves in their natural expression. The correct material "
            "for each function is used without apology or decoration. Where ergonomics and "
            "aesthetics conflict, the resolution is always a new geometry that makes them the "
            "same question. Warmth is structural, never applied. The object rewards sustained "
            "physical acquaintance."
        ),
        "material_tendency": "honest material expression — translucent where light is functional, rubber-like where grip is functional, structural material only where strength is functional; no coating for aesthetics alone",
        "geometric_tendency": "compound ergonomic curves derived from body study; organic where contacted; rectilinear where structural; the seam between materials is a designed junction",
        "optimizes_for": "total integration of ergonomic and aesthetic appropriateness — the object fits the use and the use fits the object",
        "sacrifices": "futurism, visual drama, deliberate excess",
    },
    {
        "archetype_id": "design_mead",
        "label": "Mead",
        "figure": "Syd Mead",
        "reference": "Concept design for Blade Runner, Tron, 2001, and aerospace clients",
        "design_language": (
            "Objects from a credible near-future where the physics actually worked out. "
            "Form implies a function that is slightly more advanced than the context requires — "
            "as though the engineering problem was solved two generations from now and the result "
            "is now in the hands of someone in the present. Details at every scale imply "
            "internal complexity. Light is treated as a structural material."
        ),
        "material_tendency": "stratified surfaces — matte structural body with polished or translucent inserts at energy/output points; chrome or specular elements only at kinetic interfaces; depth through surface layering",
        "geometric_tendency": "volumes taper toward the functional output point; kinetic flanges; surfaces that imply internal sectioning; silhouette communicates energy direction",
        "optimizes_for": "conviction — the object proves its own premise through visual plausibility; the viewer believes the physics",
        "sacrifices": "ergonomic accessibility, material economy, restraint",
    },
    {
        "archetype_id": "design_fukasawa",
        "label": "Fukasawa",
        "figure": "Naoto Fukasawa",
        "reference": "Super Normal movement; Muji, ±0, B&O collaborations",
        "design_language": (
            "Designed for unconscious use. The correct affordance is so obvious that no one "
            "has to think about it. Volume, weight distribution, and surface finish communicate "
            "all the user needs before the first conscious thought. Nothing is added for "
            "appearance — appearance is achieved by removing everything that does not serve "
            "behavior. The object is the shape you'd draw as a child before you knew the word."
        ),
        "material_tendency": "soft-touch matte surfaces; weight-forward distribution where the mechanism lives; subtle texture only at primary contact zones; color single and unsurprising",
        "geometric_tendency": "primary geometric volumes — sphere, cylinder, elongated prism — selected for affordance clarity; no secondary geometry without behavioral justification",
        "optimizes_for": "intuitive first contact — within one second of holding the object, the user knows exactly how to use it",
        "sacrifices": "visual complexity, spectacle, technical self-narration, uniqueness",
    },
    {
        "archetype_id": "design_starck",
        "label": "Starck",
        "figure": "Philippe Starck",
        "reference": "Juicy Salif, Ghost chair, and industrial provocation work 1980s–present",
        "design_language": (
            "Design as hypothesis. Every object asks a question the user didn't know they had. "
            "Unexpected material juxtapositions, anthropomorphic or zoomorphic geometry, and "
            "formal wit that makes the user laugh before they understand how it works. "
            "Function is present and complete — the humor is never at the expense of utility — "
            "but form arrives from a direction no conventional brief would have specified."
        ),
        "material_tendency": "material contrast for conceptual effect — chrome where you expected matte, rubber where you expected rigid, translucent where you expected opaque; material choice is an argument",
        "geometric_tendency": "anthropomorphic or non-obvious formal metaphors; legs where you'd expect a base; grip geometry that suggests a different object; visual surprise on first approach, ergonomic sense on contact",
        "optimizes_for": "memorability — the object is impossible to forget and generates conversation at first sight",
        "sacrifices": "visual restraint, cognitive invisibility, material economy",
    },
]
for _a in DESIGN_ARCHETYPES:
    _a["category"] = "Design Visionary"


def design_archetype_by_id(archetype_id: str) -> Mapping[str, Any] | None:
    for archetype in DESIGN_ARCHETYPES:
        if archetype.get("archetype_id") == archetype_id:
            return archetype
    return None


def template_drafter_prompt_archetype_first(
    *,
    problem_need: str,
    audience: str,
    association_words: Sequence[str],
    selected_archetype: Mapping[str, Any],
) -> str:
    a, b, c = _three_associations(association_words)
    return dedent(
        f"""
        You are the Template Drafter for an archetype-first Pitch Synthase workshop.

        This is a prompt-response experiment, not a full deck build. You are generating
        four competing visual alignments for the same pitch context.

        Generative frame:
        Four different competing companies each developed a solution to the same stated
        problem. Each successfully pitched its solution to the same target audience. All
        four pitches embodied the selected founder archetype and the three requested vibes,
        but each company discovered a different visual and rhetorical alignment through
        which that archetype became credible in this domain.

        "Successful" describes presentation authority and visual charisma only. It does not
        authorize fabricated claims about traction, approvals, realized revenue, customers,
        deployments, scientific validation, or product performance.

        Pitch context
        -------------
        Problem / need addressed:
        {problem_need}

        Target audience:
        {audience}

        Three vibes:
        1. {a}
        2. {b}
        3. {c}

        Selected founder archetype:
        {_json(selected_archetype)}

        Required invention
        ------------------
        Invent exactly four alignments. Do not use any fixed taxonomy or predefined visual
        focus pool. Each alignment must:
        - have a unique two- or three-word label;
        - not be a company name;
        - not repeat or paraphrase the archetype label;
        - represent a complete visual doctrine, not a slide topic;
        - remain credible for the same audience, problem, archetype, and vibes;
        - differ materially from the other three across artifact tradition, typography,
          composition, material language, diagram treatment, rhetorical temperature, and
          evidence posture.

        Output contract
        ---------------
        Return strict JSON only.
        The top-level object must contain exactly one key: "candidates".
        Return exactly four candidates with candidate_id values tmpl_1 through tmpl_4.

        Each candidate must contain:
        - candidate_id
        - label
        - description
        - visual_doctrine
        - style_tags
        - image_prompt

        Label rules:
        - two or three words only;
        - unique case-insensitively;
        - not the name of a company, product, or brand;
        - must not appear verbatim inside that same candidate's image_prompt.

        Image prompt rules:
        - describe one real photographed conference-room slide on screen;
        - presenter offscreen;
        - mid-presentation artifact, not a cover slide or mood board;
        - representative domain content derived from the stated problem;
        - no real company names;
        - no invented traction, approval, customer, realized-revenue, deployment, or validation claims;
        - no visible use of the alignment label;
        - no visible use of the archetype label as slide copy or branding;
        - long enough and specific enough to produce a memorable, domain-aware slide.

        JSON shape:
        {{
          "candidates": [
            {{
              "candidate_id": "tmpl_1",
              "label": "Example Label",
              "description": "One sentence explaining how this alignment makes the archetype credible for this audience and problem.",
              "visual_doctrine": "Artifact tradition, typographic behavior, material language, composition, and evidence posture.",
              "style_tags": ["tag one", "tag two", "tag three", "tag four"],
              "image_prompt": "Photograph one real conference-room slide on screen..."
            }}
          ]
        }}
        """
    ).strip()


def pitch_elaborator_prompt(*, rough_input: str) -> str:
    """
    The "write my whole pitch" button: takes whatever scrap the user gave --
    a phrase, a vague claim, a fragment -- and ghostwrites a complete,
    specific, mechanism-rich elevator pitch in the same problem/solution
    register as a strong real pitch. This is explicitly a reviewable DRAFT:
    invented specifics must read as a plausible, internally consistent
    proposal, never as verified fact, and the output must flag what it
    invented so the user can correct it before anything downstream runs.
    """
    return dedent(
        f"""
        You are reconstructing the pitch that ultimately ended up succeeding, from
        whatever the user half-attempted to say. Treat their rough input as a
        real, sincere attempt at gesturing toward a real idea, even if it's just a
        fragment or one vague sentence. The user will review and edit everything
        you write before it is used -- your job is to hand them a strong, complete
        starting draft of that successful pitch, not a final claim.

        Write a full problem/solution elevator pitch: name the actual failure
        mode of the status quo, then describe a specific, plausible mechanism
        for how the proposed thing works -- a real causal chain, not just an
        assertion that it works. Invent concrete, internally consistent
        specifics (a named mechanism, a plausible number, a specific component)
        wherever the input doesn't supply them -- a vague pitch with invented
        specifics is more useful as a draft than a vague pitch with none, as
        long as it's clearly marked as invented.

        Exception -- true nonsense: if the input is not a sincere attempt at any
        real idea at all (gibberish, random characters, test input, or otherwise
        unsalvageable), do not force a fake serious mechanism onto it and do not
        reach for an unrelated joke. Instead, write the placeholder pitch AS a
        real problem/solution pitch for the exact meta-problem that just
        happened: someone was unable to get a coherent, sincere idea typed into
        a relatively sophisticated pitch-drafting tool. Pitch a plausible
        solution to that specific failure (e.g. a draft-reconstruction
        assistant, an intent-clarification layer, a nonsense-input detector)
        in the same register and format as every other elevator pitch you
        write -- self-referential to what just happened, not a generic gag.
        Say so plainly in "reason_for_placeholder". Use your judgment: a genuinely thin
        but sincere idea ("make sound louder without power") gets the real
        reconstruction treatment above, not the joke placeholder.

        Rough input from the user:
        {rough_input}

        Output contract
        ---------------
        Return strict JSON only:
        {{
          "is_nonsense": true or false,
          "elevator_pitch": "complete problem/solution pitch, 3-6 sentences, "
                             "specific and mechanism-rich (or the cheeky "
                             "placeholder pitch if is_nonsense is true)",
          "invented_elements": ["short phrase naming each specific you invented "
                                 "that the user should verify or replace"],
          "conveys_suggestion": "one sentence suggesting what this pitch should "
                                 "convey to its audience",
          "reason_for_placeholder": "empty string unless is_nonsense is true, "
                                     "then one sentence explaining why"
        }}
        """
    ).strip()


def pitch_quality_gate_prompt(
    *,
    elevator_pitch: str,
    doc_text: str | None = None,
) -> str:
    """
    Cheap pre-check before the approach drafter runs. Catches thin/placeholder
    input that would otherwise get confidently but wrongly elaborated into a
    plausible-sounding invented mechanism. Text-only, no images -- meant to be
    negligible cost compared to the approach-drafting call it gates.
    """
    doc_block = f"\n\nSupporting document:\n{doc_text}" if doc_text else ""
    return dedent(
        f"""
        You are a pre-check gate, not a pitch writer. Decide whether the text below
        contains enough real, specific information to ground four faithful strategic
        pitch approaches -- or whether it is a placeholder, a one-line stub, or a
        vague claim with no actual mechanism, causal explanation, or specifics.

        A real elevator pitch does NOT need to be long, polished, or investor-ready.
        It DOES need at least one of: a specific mechanism or how it works, a named
        component/process/quantity, or a concrete causal chain (why the problem
        happens, why the solution addresses it). A single vague sentence asserting
        a gap exists ("there isn't a way to X") without saying anything about HOW
        the proposed thing works is NOT enough, even if it sounds confident.

        Elevator pitch:
        {elevator_pitch}
        {doc_block}

        Output contract
        ---------------
        Return strict JSON only, one top-level object:
        {{
          "is_real_pitch": true or false,
          "reason": "one sentence on why this does or does not have enough to work with",
          "missing_elements": ["short phrase", "..."]
        }}
        "missing_elements" should be empty if is_real_pitch is true. If false, name
        what's missing in terms the user can act on (e.g. "no mechanism or how it
        works", "no specifics about the actual invention", "just a claim, no causal
        explanation").
        """
    ).strip()


def approach_drafter_prompt(
    *,
    audience: str,
    elevator_pitch: str,
    conveys: str,
    selected_archetype: Mapping[str, Any] | None,
    doc_text: str | None = None,
    excepted_inference_elements: Sequence[str] | None = None,
    has_brand_reference: bool = False,
    association_words: Sequence[str] | None = None,
    design_philosophies: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """
    Full-pitch-first workshop experiment: receives the REAL elevator pitch (and
    optional supporting document), returns four strategic pitch approaches as
    text only -- no images.

    Uses a two-archetype blend system. The primary archetype is fixed by the
    user or auto-chosen by the drafter. The drafter always auto-selects the
    single best complementary secondary archetype for this specific pitch.
    The four approaches are the four blend combinations: PP, PS, SP, SS.
    """
    catalog_block = "\n\n".join(
        f"- {a['label']}, {a['role']} [{a['archetype_id']}]: {a['win_condition']}"
        for a in FOUNDER_ARCHETYPES
    )
    if selected_archetype is not None:
        primary_instruction = (
            f"The user has requested this archetype be represented:\n{_json(selected_archetype)}\n\n"
            f"At least one of the four approach pairs must include it. It does not need to "
            f"appear in all four.\n\n"
            f"Archetype catalog:\n{catalog_block}"
        )
    else:
        primary_instruction = (
            "No archetype was specified. Choose freely from the catalog below.\n\n"
            f"Archetype catalog:\n{catalog_block}"
        )
    doc_block = f"\n\nSupporting document (uploaded by the user):\n{doc_text}" if doc_text else ""
    if design_philosophies:
        _shared = "\n".join(
            f"  [{d.get('design_id','?')}] {d.get('assigned_archetype_id','?')}: "
            f"{d.get('physical_description','')[:200]}"
            for d in design_philosophies
        )
        design_context_block = dedent(f"""

        Physical prototype context
        --------------------------
        Engineering has independently produced four prototype designs for this
        mechanism. The user has not yet chosen one. Your approaches will each
        be built around whichever prototype the user ultimately selects.

        Use these physical descriptions to ensure your narrative choices are
        grounded in what the device can actually be — not to constrain the
        commercial angle, but to ensure each approach is compatible with the
        shared physical invariants present across all four designs.

        {_shared}

        Do NOT optimize approaches toward or against any specific design above.
        Optimize against what they have in common — the physical constraints
        that will be present regardless of which design the user chooses.
        """)
    else:
        design_context_block = ""
    brand_ref_block = (
        "\n\nBrand reference\n"
        "---------------\n"
        "The user has provided an existing brand/visual identity reference (a website, "
        "UI design, or marketing material) alongside this pitch — you will see the image "
        "in this message. Each approach's visual_direction should be grounded in that "
        "established identity: evolve or adapt it to suit the approach's archetype emphasis "
        "rather than inventing a visual language from scratch."
    ) if has_brand_reference else ""
    inference_policy = _inference_policy_block(excepted_inference_elements)
    _aw = list(association_words or [])
    vibes_block = (
        "\n        Aesthetic direction (user-declared vibes)\n"
        "        ------------------------------------------\n"
        "        The user has named three aesthetic qualities they want the deck to embody:\n"
        + "".join(f"        {i+1}. {w}\n" for i, w in enumerate(_aw[:3]))
        + "        These are not content instructions — they are aesthetic pressure applied\n"
        "        equally across all four approaches. Each approach must bend them toward\n"
        "        its own archetype logic: the same vibe word means something different\n"
        "        inside a Barnum pairing than inside a Chanel one. The vibe_semantics\n"
        "        field is where you state what each vibe specifically means for this approach."
    ) if _aw else ""

    return dedent(
        f"""
        {inference_policy}

        You are drafting four competing strategic pitch approaches for the same real
        invention and audience. Each approach is the interference sum of the shared
        primary archetype with a different secondary archetype.

        This is a prompt-response experiment, not a full deck build. Nothing here
        generates images. Ground every approach in the real mechanism and claims the
        elevator pitch describes. Do not invent a different mechanism, EXCEPT for any
        element named in the authorized inference exception above.

        Archetype selection
        -------------------
        {primary_instruction}

        Each approach gets its OWN independent pair (A, B) — there is no shared
        primary across all four. Choose four distinct pairs that maximize strategic
        and visual variety across the set. The pairs (Athena+Odysseus) and
        (Odysseus+Athena) are the same pair — order does not matter for uniqueness.
        No pair may appear more than once across the four approaches.

        One approach MAY use the same archetype for both A and B (e.g. Athena+Athena).
        This means full constructive interference: that archetype's win condition
        pushed to its functional extreme, rhetorical temperature at maximum, evidence
        posture at its most committed form. Use it for at most one approach.

        Interference rule
        -----------------
        Each approach is the waveform interference sum of its pair (A, B):

        - Where A and B CONFLICT on a dimension: that dimension flattens — treat it
          neutrally, avoid forcing either extreme.
        - Where A and B SYNERGIZE on a dimension: that dimension amplifies — express
          it with higher confidence, more direct language, the bolder form of the claim.
        - If A == B: every dimension is constructive interference at maximum amplitude.

        Pitch context
        -------------
        Audience:
        {audience}

        Elevator pitch (the real invention -- ground every approach in this):
        {elevator_pitch}

        What the user wants this pitch to convey:
        {conveys}
        {doc_block}
        {brand_ref_block}
        {design_context_block}
        {vibes_block}

        Output contract
        ---------------
        Return strict JSON only. Top-level key: "approaches" only — no shared
        primary_archetype at the top level.
        "approaches": exactly four objects, approach_id values approach_1 through
        approach_4. Each approach object contains:
        - approach_id
        - archetype_a: full archetype object for the first archetype in this pair
        - archetype_b: full archetype object for the second (may equal archetype_a
          for the one doubled approach, if used)
        - effective_archetype_id: the archetype_id of whichever of the two is
          dominant in the interference sum (or the shared one if A == B)
        - label: unique two- or three-word label; not a company name; not an archetype label
        - pitch_angle: one to two sentences on the strategic emphasis and why it wins
        - key_differentiator: one sentence on what makes this approach distinct from
          the other three
        - visual_direction: artifact tradition, typography, material language, and
          composition -- enough for a later call to write a single hero-slide image
          prompt from this alone
        - vibe_semantics: exactly three objects, each with:
            - vibe: a two- to four-word aesthetic or tonal quality this approach embodies
            - interpretation: one sentence explaining how that quality is specifically
              expressed through this approach's archetype pairing and pitch angle —
              not as a general mood descriptor but as a direct consequence of the
              interference logic; something a designer could act on

        JSON shape:
        {{
          "approaches": [
            {{
              "approach_id": "approach_1",
              "archetype_a": {{"archetype_id": "...", "label": "...", "role": "...", "posture": "...", "win_condition": "..."}},
              "archetype_b": {{"archetype_id": "...", "label": "...", "role": "...", "posture": "...", "win_condition": "..."}},
              "effective_archetype_id": "...",
              "label": "...",
              "pitch_angle": "...",
              "key_differentiator": "...",
              "visual_direction": "...",
              "vibe_semantics": [
                {{"vibe": "...", "interpretation": "..."}},
                {{"vibe": "...", "interpretation": "..."}},
                {{"vibe": "...", "interpretation": "..."}}
              ]
            }}
          ]
        }}
        """
    ).strip()


def approach_regenerate_prompt(
    *,
    audience: str,
    elevator_pitch: str,
    conveys: str,
    selected_archetype: Mapping[str, Any],
    doc_text: str | None,
    keep_approaches: Sequence[Mapping[str, Any]],
    regenerate_ids: Sequence[str],
) -> str:
    """Regenerate only the flagged approach_id(s), holding the rest fixed. The
    kept approaches are given as context so the new one(s) don't collide with
    what the user already liked."""
    doc_block = f"\n\nSupporting document (uploaded by the user):\n{doc_text}" if doc_text else ""
    return dedent(
        f"""
        You are regenerating specific strategic pitch approaches within an existing
        batch of four, for the same real invention, audience, and founder archetype.
        The approaches the user is keeping are shown below. Generate ONLY replacements
        for the flagged approach_id(s). The new approach(es) must still differ from the
        kept ones across: which claim leads, evidence posture, rhetorical temperature,
        and visual doctrine. Do not repeat a kept approach's label or visual direction.

        Pitch context
        -------------
        Audience:
        {audience}

        Elevator pitch (ground the new approach(es) in this, no invented mechanism):
        {elevator_pitch}

        What the user wants this pitch to convey:
        {conveys}
        {doc_block}

        Founder archetype (fixed, unchanged):
        {_json(selected_archetype)}

        Approaches being kept (do not regenerate these; do not collide with them):
        {_json(list(keep_approaches))}

        Approach IDs to regenerate: {list(regenerate_ids)}

        Output contract
        ---------------
        Return strict JSON only. One top-level key: "approaches" -- a list containing
        ONLY the newly generated replacement approach(es), same shape as before:
        approach_id, label, pitch_angle, key_differentiator, visual_direction.
        """
    ).strip()


def template_drafter_prompt(
    *,
    problem_need: str,
    audience: str,
    association_words: Sequence[str],
) -> str:
    """
    Select and describe four founder archetypes + four visual focuses for the pitch context.

    Receives only problem/need, audience, and associations. Archetypes are returned as
    text only (no images). Visual focuses get one image prompt each — the template images
    the user will choose between. Both selections are tuned to the specific domain.
    """
    a, b, c = _three_associations(association_words)
    archetypes_block = "\n\n".join(
        f"{i+1}. {arch['label']} [{arch['archetype_id']}]\n   {arch['posture']}"
        for i, arch in enumerate(FOUNDER_ARCHETYPES)
    )
    focuses_block = "\n\n".join(
        f"{i+1}. {foc['label']} [{foc['focus_id']}]\n   {foc['description']}"
        for i, foc in enumerate(VISUAL_FOCUSES)
    )
    return dedent(
        f"""
        You are the Template Drafter for Pitch Synthase.

        You will make two independent selections for this pitch context, then write
        image prompts for the visual focus selection only.

        SELECTION 1 — FOUNDER ARCHETYPES (no images generated for these)
        Select the four potential founder posture archetypes, from the pool below, that are
        most naturally suited to making a persuasive, coherent, knowledgable, and outright effective
        pitch given the user-specified's domain, audience, and associations.
        These represent how the founders carry themselves and how they approach the pitch; the user will pick one.
        Return only the archetype_id and label — no image prompts needed. Note that the generated 
        images for Visual Focus are agnostic to any one Founder Archetype but are still optimized for effectiveness.

        SELECTION 2 — VISUAL FOCUSES (one template image generated per selection)
        Select the four visual focus types, from the pool below, that are most
        effective for this pitch context. These determine how the deck will actually make
        its argument for the pitch itself; they do not constrain all visual elements of the 
        deck. (eg. "Mechanism" would focus on the details of how the pitch works from a 
        first-principles causal standpoint, but the deck would not only consist of technical
        diagrams). For each selected focus, write one image_prompt directing the image model 
        to photograph a real slide from a pitch deck, with both the deck design and presentation 
        venue optimized for the pitch domain and audiencee. The visual language must reflect what that
        focus looks like in this industry, in its ideal form both aesthetically and structurally. Each
        one is framed as being pitched by a team of four founders, each briefly described with a background and
        temperament aligned to each of the four potential archetypes as they would manifest in that domain.

        Key principle: the same focus looks different across domains, as does the optimally persuasive 
        presentation organized around it. The difference between a lecture and a pitch is that the former merely 
        conveys information, but the latter must make the audience *excited* about that information. 
        We are creating a pitch.  

        Pitch context
        -------------
        Problem / need addressed:
        {problem_need}

        Target audience:
        {audience}

        Three key associations to convey:
        1. {a}
        2. {b}
        3. {c}

        Available founder archetypes (select 4)
        ----------------------------------------
        {archetypes_block}

        Available visual focuses (select 4, generate image prompts)
        ------------------------------------------------------------
        {focuses_block}

        Image prompt instructions (for visual focuses only):
        - Photograph one real slide on screen, presenter offscreen, in a venue tailored to the user inputs.
        - Visible text should be representative of the domain (derived from need addressed) — no product-specific
          copy, no factual claims, no real company names.
        - Exactly one photographed slide artifact, not a flat mockup or editable export.
        - The slide's content, layout, and visual language reflect the visual focus type, optimized to this specific       
          domain, tailored to this specific audience, designed to be spectacularly engaging and visually distinctive.
        - The photographed slides should represent exemplars of outstanding quality in every regard; all were part of
          separate pitches given by competing companies, all offering similar solutions to the specified problem while 
          structuring their proposals differently to align with their respective focus, and all were wildly successful.
        - The pitches shown in the photographs were considered by all in attendance to be logically brilliant and    
          visually unforgettable, designed by teams who knew exactly how to appeal to this audience in a live 
          presentation format.

        Critical constraints:
        - Return strict JSON only. No prose before or after.
        - The JSON object must have exactly two top-level keys: "candidates" and "selected_archetypes".
          Both are required. Omitting either key will break the pipeline.
        - "candidates": exactly four objects in your chosen order.
          Each must have: candidate_id, focus_id, label, description, style_tags,
          image_prompt.
        - candidate_id values must be tmpl_1, tmpl_2, tmpl_3, tmpl_4 in order.
        - "selected_archetypes": exactly four objects in your chosen order.
          Each must have: archetype_id, label.
        - Write candidates first (they are longer), then selected_archetypes last.

        JSON shape:
        {{
          "candidates": [
            {{
              "candidate_id": "tmpl_1",
              "focus_id": "focus_mechanism",
              "label": "Mechanism",
              "description": "One sentence: what this focus shows this audience.",
              "style_tags": ["...", "..."],
              "image_prompt": "Photograph of a single slide as seen by an audience member..."
            }}
          ],
          "selected_archetypes": [
            {{
              "archetype_id": "arch_caesar",
              "label": "Caesar"
            }}
          ]
        }}
        """
    ).strip()


def design_drafter_prompt(
    *,
    elevator_pitch: str,
    doc_text: str | None = None,
    excepted_inference_elements: Sequence[str] | None = None,
) -> str:
    """
    Generate four independent prototype designs from product description alone.

    Engineering optimization target: material physics and functional geometry.
    No marketing input. No approach context. The designs are selected and
    developed purely to explore the space of physically valid, meaningfully
    differentiated realizations of the stated mechanism.

    Runs before and independently of approach drafting. Output is stored as
    prototype_candidates_json and read by the approach drafter for physical
    grounding — not to constrain narrative, but to ensure commercial claims
    are consistent with what the device can actually be.
    """
    doc_block = f"\n\nSupporting document:\n{doc_text}" if doc_text else ""
    inference_policy = _inference_policy_block(excepted_inference_elements)
    catalog_block = "\n\n".join(
        f"[{a['archetype_id']}] {a['label']} ({a['figure']}, {a['reference']})\n"
        f"Design language: {a['design_language']}\n"
        f"Material tendency: {a['material_tendency']}\n"
        f"Geometric tendency: {a['geometric_tendency']}\n"
        f"Optimizes for: {a['optimizes_for']}\n"
        f"Sacrifices: {a['sacrifices']}"
        for a in DESIGN_ARCHETYPES
    )
    return dedent(
        f"""
        {inference_policy}

        You are an engineering design team generating four distinct physical
        prototypes of the same underlying mechanism.

        These are not four different products. They are the SAME mechanism —
        expressed through four DISTINCT design philosophy archetypes, the way
        four different engineering teams, starting from the same physics, would
        each have built a different prototype shaped by their own material
        priorities, geometric instincts, and serendipitous discoveries during
        development.

        Your optimization target is physical plausibility and engineering
        differentiation. You have no information about and no interest in how
        this product will be marketed. Select archetypes based on which design
        philosophies will produce the most meaningfully different physical
        realizations of this specific mechanism — not on any commercial angle.

        HARD CONSTRAINTS — treat as physical law:
        All constraints stated in the product description apply to ALL four
        designs without exception. If the description specifies a material,
        an ergonomic requirement, a safety constraint, a form factor, or a
        dimension, that is binding. Do not introduce geometry, materials, or
        proportions that contradict the stated description or physical reality.
        No impossible geometry. No materials that cannot satisfy the function.

        DESIGN ARCHETYPE CATALOG:
        Select exactly 4 of the following 6 archetypes — the 4 whose design
        philosophies will produce the most physically differentiated prototypes
        for THIS specific mechanism. No two designs may share an archetype.

        Maximize range of material tendency, geometric approach, and surface
        character across the four. Each design must be a distinct engineering
        decision path — not a color variant or scale variant of another.

        {catalog_block}

        Product description
        -------------------
        {elevator_pitch}
        {doc_block}

        Output contract
        ---------------
        Return strict JSON only. Top-level keys: "archetype_selection_rationale"
        and "designs".

        "archetype_selection_rationale": one or two sentences explaining which
        4 archetypes you selected and why they produce maximum physical
        differentiation for this specific mechanism.

        "designs": exactly four objects, numbered design_1 through design_4.

        Each design object:
        - design_id: "design_1" through "design_4"
        - assigned_archetype_id: the archetype_id from the catalog
        - design_philosophy: 2–4 sentences. The engineering origin story —
          what priority the team held highest, what tradeoff shaped the form
          factor, what constraint or discovery determined a specific geometric
          decision. Physical plausibility only. No marketing language.
        - physical_description: precise visual description of the finished
          prototype. Material finishes, color, proportions, specific geometric
          features, visible mechanism elements, and how they relate spatially.
          Detailed enough to anchor consistent image generation.
          No brand names or visible text on device.
        - reference_image_prompt: complete, self-contained prompt for a clean
          studio product reference shot. White or near-white background,
          three-quarter front view, full device visible, soft diffused studio
          lighting, no human hands, no scene context, no props.
          Must embed the full physical_description inline — used standalone.
          Photorealistic product photography. No text, callouts, or labels.

        JSON shape:
        {{
          "archetype_selection_rationale": "...",
          "designs": [
            {{
              "design_id": "design_1",
              "assigned_archetype_id": "<one archetype_id from the catalog above>",
              "design_philosophy": "...",
              "physical_description": "...",
              "reference_image_prompt": "..."
            }}
          ]
        }}
        """
    ).strip()


def single_slide_drafter_prompt(
    *,
    audience: str,
    elevator_pitch: str,
    approach: Mapping[str, Any],
    archetype_a: Mapping[str, Any],
    archetype_b: Mapping[str, Any],
    doc_text: str | None = None,
    excepted_inference_elements: Sequence[str] | None = None,
) -> str:
    """
    Merged Anchor-Writer/Template-Drafter role for the full-pitch-first workshop:
    given one approved strategic approach plus the real elevator pitch, write the
    single most persuasive hero slide for that approach. Unlike the template-stage
    prompts, this one IS grounded in the real mechanism and claims -- that grounding
    is the entire point of this design. It still may not fabricate business proof
    (traction, customers, realized revenue, approvals) beyond what the elevator
    pitch/doc actually states. A pricing exception authorizes proposed offer
    pricing and related unit economics, never fabricated business proof.
    """
    doc_block = f"\n\nSupporting document (uploaded by the user):\n{doc_text}" if doc_text else ""
    inference_policy = _inference_policy_block(excepted_inference_elements)
    return dedent(
        f"""
        {inference_policy}

        You are writing the single hero slide that best proves out one specific,
        already-approved strategic approach for a real pitch. This is a prompt-response
        workshop experiment: one text call, output feeds a single image generation call.

        Ground everything in the real elevator pitch below -- this is the actual
        invention, not a placeholder. You may state its real mechanism and claims.
        You may NOT invent business proof the pitch does not state: no fabricated
        customers, realized revenue, traction, approvals, partnerships, or
        deployments. A pricing inference exception, if present, authorizes only
        proposed offer pricing and related unit economics; it never authorizes
        fabricated business proof.

        Audience:
        {audience}

        Elevator pitch (real invention):
        {elevator_pitch}
        {doc_block}

        Archetype pair for this approach (interference blend):
        A: {_json(dict(archetype_a))}
        B: {_json(dict(archetype_b))}

        Approved approach to realize as one slide:
        {_json(dict(approach))}

        Output contract
        ---------------
        Return strict JSON only. One top-level key: "image_prompt".
        The image_prompt must:
        - direct a photograph of one real conference-room slide on screen, presenter offscreen;
        - be a mid-presentation artifact, not a cover slide or mood board;
        - realize this approach's visual_direction specifically, not a generic pitch slide;
        - reflect the real mechanism/claims from the elevator pitch above;
        - not fabricate traction, customers, realized revenue, approvals, partnerships, or deployments
          beyond what the elevator pitch/document states;
        - not print the approach's label or the archetype's label as visible slide branding;
        - be long enough and specific enough to produce one memorable, on-topic slide.
        """
    ).strip()


def single_slide_image_prompt(*, slide_draft: Mapping[str, Any]) -> str:
    image_prompt = str(slide_draft.get("image_prompt") or "").strip()
    if len(image_prompt) < 80:
        raise ValueError("Single-slide draft is missing a usable image_prompt.")
    return dedent(
        f"""
        {image_prompt}

        Binding output constraints:
        - Generate exactly one image.
        - Do not create an editable slide export or flat mockup; render a real
          photographed slide artifact.
        - Do not fabricate traction, customer names, realized revenue, approvals, or
          deployments beyond what was explicitly given.
        """
    ).strip()


def template_image_prompt(*, candidate: Mapping[str, Any]) -> str:
    image_prompt = str(candidate.get("image_prompt") or "").strip()
    if len(image_prompt) < 80:
        raise ValueError(f"Template candidate {candidate.get('candidate_id') or '?'} is missing a usable image_prompt.")
    return dedent(
        f"""
        {image_prompt}

        Binding output constraints:
        - Generate exactly one image.
        - The image is a visual template candidate only.
        - Do not include brand-specific copy.
        - Do not include factual claims, traction, customers, approvals, revenue,
          partnerships, deployments, endorsements, or proof.
        - Do not create an editable slide export or flat mockup; render a real
          photographed slide artifact.
        """
    ).strip()


def candidate_prompt(
    audience: str,
    association_words: Sequence[str],
    problem_need: str = "the stated problem or need",
) -> str:
    """Compatibility wrapper for older call sites. Prefer template_drafter_prompt()."""
    return template_drafter_prompt(
        problem_need=problem_need,
        audience=audience,
        association_words=association_words,
    )


# ---------------------------------------------------------------------------
# LEGACY COMPATIBILITY — fictional Anchor Writer (not used by generation_worker)
# ---------------------------------------------------------------------------

def anchor_writer_prompt(
    *,
    mode: str,
    user_inputs: Mapping[str, Any],
    selected_template: Mapping[str, Any],
    paid_slide_count_value: int | None = None,
    supporting_document: Any | None = None,
    supporting_image: Any | None = None,
    design_philosophy: str | None = None,
) -> str:
    """
    Prompt the legacy fictional Anchor Writer.

    This compatibility/A-B API is not the active Canonical Anchor. It must not
    create slide copy,
    storyboard content, a deck outline, per-slide prompts, or a transcript. Its
    output schema is exactly {"user_inputs", "deck_generation_prompt"}.
    """
    mode = normalize_mode(mode)
    if not isinstance(user_inputs, Mapping):
        raise TypeError("user_inputs must be the raw structured user input object")
    if not isinstance(selected_template, Mapping):
        raise TypeError("selected_template must be the selected template object")

    if mode in {"PAID", "CONVERT"}:
        # Validated for its side effect (must be a real 4-10 explicit choice); the
        # number itself is intentionally not embedded in this fictional framing
        # prompt. The one place slide count is stated explicitly is the artifact
        # request block the calling application appends afterward.
        paid_slide_count(explicit_slide_count=paid_slide_count_value)
        if mode == "CONVERT":
            branch_instruction = dedent(
                f"""
                If MODE is CONVERT:
                - The prompt must describe a complete presentation in enough imaginative
                  detail that any individual slide from it could be convincingly
                  visualized. The deck will ultimately contain as many slides as are
                  generated from it — do not anchor the fiction to a specific slide count.
                - Do not reference which slides will be generated, requested, or exposed. Do not include
                  slide numbers, artifact counts, or any output instructions. The calling application
                  specifies all of that.
                - Do not tell the next model it is generating a paid deck or conversion.
                - Additionally: photographs from a preview of this pitch are provided alongside this prompt as input images. They are real slides from this same presentation. Their positions within the full deck sequence are not specified and must not be assumed. The prompt must instruct the next model to:
                  (1) treat the provided preview photographs as fixed slides belonging to this presentation — determine where each fits best within the deck argument and include it there;
                  (2) use those photographs as the governing visual style reference for the full deck, in place of any separate template image;
                  (3) generate the remaining slides to complete the deck, matching the visual language of the provided photographs exactly.
                - Do not number or label the preview photographs by slide position.
                """
            ).strip()
        else:
            # Detect when no visual-direction metadata is available at all (injected jobs,
            # legacy adapters, or incomplete worker payloads). The Deck Builder owns the
            # final visual grammar, but if it receives no direction, the anchor must give
            # it enough aesthetic pressure to prevent per-slide style drift.
            archetype = selected_template.get("archetype") or {}
            has_visual_direction = bool(
                archetype.get("posture")
                or archetype.get("label")
                or selected_template.get("focus_id")
                or selected_template.get("label")
                or selected_template.get("description")
                or selected_template.get("style_tags")
                or selected_template.get("template_manifest")
                or selected_template.get("visual_style_contract")
                or selected_template.get("template_image")
                or selected_template.get("template_image_id")
            )
            visual_anchor_clause = "" if has_visual_direction else dedent(
                """
                - No selected visual focus, archetype, template image, or visual style contract is
                  provided. You must compensate by embedding a vivid and specific visual aesthetic
                  description directly in the deck_generation_prompt. Describe: background color and
                  material, accent color palette, typographic register (serif/mono/sans, weight, case
                  treatment), information density, diagram style, and overall presentational posture.
                  Be precise enough that the next model can synthesize one coherent deck-specific
                  visual grammar instead of letting each slide invent its own visual language.
                """
            ).strip()
            branch_instruction = dedent(
                f"""
                If MODE is PAID:
                - Describe the deck as a complete presentation in enough imaginative detail
                  that any individual slide from it could be convincingly visualized. The
                  deck will ultimately contain as many slides as are generated from it — do
                  not anchor the fiction to a specific slide count.
                - Do not reference which slides will be generated, requested, or exposed. Do not include
                  slide numbers, artifact counts, or any output instructions. The calling application
                  specifies all of that.
                - Do not tell the next model it is generating a paid deck.
                {visual_anchor_clause}
                """
            ).strip()
    else:
        branch_instruction = dedent(
            f"""
            If MODE is PREVIEW:
            - Describe the deck as a complete {PREVIEW_LATENT_SLIDE_COUNT}-slide presentation in enough
              imaginative detail that any individual slide from it could be convincingly visualized.
            - Do not reference which slides will be generated, requested, or exposed. Do not include
              slide numbers, artifact counts, or any output instructions. The calling application
              specifies all of that.
            - Do not tell the next model it is generating a preview.
            """
        ).strip()

    input_packet = {
        "MODE": mode,
        "user_inputs": user_inputs,
        "selected_archetype": selected_template.get("archetype") or {},
        "selected_visual_focus": {
            k: v for k, v in selected_template.items() if k != "archetype"
        },
        "supporting_document": supporting_document,
        "supporting_image": supporting_image,
        **({"product_design_philosophy": design_philosophy} if design_philosophy else {}),
    }

    return dedent(
        f"""
        You are the generative anchor writer for Pitch Synthase, a tool
        which produces entire agency-polished pitch decks purely from user input
        fields and API calls, no manual design step necessary.

        The key to making this method work is generative anchoring: using
        artifact-and-audience-based selective pressure to implicitly shape output
        coherence and quality, and constructing that output following a template which is
        itself generated as though the desired end result already existed in its most optimal possible form.

        You will receive structured user inputs, selected visual-focus information,
        selected founder-archetype information, pitch materials, and any
        product/reference image information. Preserve the user inputs exactly as
        provided. Do not summarize, correct, normalize, reinterpret, rename, or
        modify the user input structure.

        Your task is to invent the fictional-but-plausible narrative presentation context
        in which this pitch deck, specific to the details provided by the user,
        has not only succeeded under the harshest relevant audience pressure, but
        has done so with a level of meticulous polish, aesthetic dynamism, and 
        pure unmitigated brilliance such that the pitch itself was considered by
        all in attendance as *genuinely unforgettable*.

        This narrative is the single fiction in the pipeline. It is the element upon
        which the output's success is wholly dependent, and must also be unforgettable.

        The selected founder archetype is not a decorative label — it is the governing
        strategic frame of the entire presentation as well as the founders. The selected
        visual focus is the lens through which the deck expresses that strategy. If the
        two ever pull in different directions, the archetype governs the pitch structure,
        founder framing, and argumentative posture, while the visual focus governs how
        that structure is expressed on the page. Together with the domain and audience,
        they determine what manner of layout and visuals are used for the presentation.
        They also imply specific aspects about who the presenters are, how they position
        themselves relative to the industry, what the deck is structured to say, how it
        is structured to say it, what it is deliberate about not saying, and what attitude
        they brought into the room. The fictional context must make that posture legible
        through the people and the pressure of the room, not by naming labels. The more
        evocative the narrative context is, the more personality and stakes and personal
        appeal is imparted on the pitch itself, and thus the more effective the resulting
        presentation.

        You do not need to explicitly label the archetype (e.g. "Barnum", "Chanel")
        anywhere in the deck_generation_prompt. Instead, your description of the founders 
        should naturally lead someone to infer that quality on their own. 

        Begin with the goal framing: this was an unforgettable pitch. Then the domain and
        problem: ask what the ideal team for this specific challenge would actually
        look like. What skills, lived experiences, and motivations would need to be in the
        room for this pitch to be given deftly and *passionately*? Construct the
        hypothetical team as a supergroup — not a generic startup founding team, but
        the specific constellation of people this problem would have selected for
        itself if it could. The founders section must be centrally framed by the
        selected archetype: their backgrounds, motives, chemistry, and way of seeing
        the problem should read as the human embodiment of that archetype's logic,
        not as a generic expert team with a little extra tone added afterward. Each
        member's presence should be purposeful and
        non-interchangeable: the person who spent a decade inside the system being
        disrupted, the technical credentialist who crossed into this domain from
        somewhere unexpected, the operator who has already run something adjacent
        at scale, the domain-native who gave up a safer path to build this instead.

        The supergroup must always include one member who is the presentation's own
        creative director — the person who actually built this deck
        by hand. Cast them the same way as every other member: purposeful and
        non-interchangeable, typecast to this specific pitch, embodying the
        archetype's visual and rhetorical logic rather than generic design-agency
        polish. This is the person whose taste and craft the deck's visuals belong
        to; the presentation was designed and produced by them, not generated.

        This member requires a specific biography across three dimensions:

        First: how they translated the archetype's strategic logic into a structural
        and compositional principle — not at the level of palette or mood, but at
        the level of grid, information hierarchy, sequence, and contrast. The
        archetype is not a visual style; it is an argument posture. A designer who
        genuinely embodies it will have made layout decisions that enact that posture
        structurally: where proof sits relative to claim, whether the deck opens
        with contrast or with authority, whether the grid is assertive or deferential,
        whether the visual hierarchy confirms the audience's expectations or
        strategically inverts them. Describe the specific compositional logic this
        designer derived from the archetype — the structural decision that makes the
        deck feel inevitable to someone who understands the argument, not just
        polished to someone who doesn't.

        Second: what specific school of artistic or design thought, combined with
        what specific past business or domain experience, made this particular person
        the only one who could have produced the magnum opus version of this pitch.
        Not a great designer generically — the specific person whose lineage happened
        to sit exactly at the intersection of the aesthetic tradition this pitch
        needed and the domain knowledge that made them fluent in what the audience
        would read as credible versus performed. A deck for a mid-90s fever-dream
        consumer toy needs someone with a specific kind of irreverence and diegetic
        instinct. A deck for a no-nonsense enterprise SaaS needs someone who knows
        how to signal "professional, not Figma plugin" to a savvy audience while
        still deploying strategic visual elements that only land with people who
        know what they're looking at. Name the lineage. Name the career background.
        Make it clear why this pitch, given to any other designer, would have been
        merely good.

        Third: their specific tool workflow and the technical or methodological
        breakthrough that elevated the deck to its final quality. Not "they used
        Figma" — the specific system, constraint, or technique that made their
        visual choices deterministic rather than invented. The equivalent of naming
        MagicaVoxel: the tool whose own affordances and defaults encode the
        constraints, so that the designer's decisions collapse into specific
        operations within a specific system rather than open-ended aesthetic
        exploration. Name what they built it in, name the method or framework they
        applied inside it, and name the specific element — the thing they figured
        out or executed — that brought the deck from accomplished to unforgettable.
        This is the mechanism that the next model is reconstructing; it must be
        specific enough that the visual choices fall out of it as consequences
        rather than being invented slide by slide.

        If a product_design_philosophy is provided in the input packet, it describes
        the specific design logic the team developed for the physical object —
        the material reasoning, geometric philosophy, and aesthetic priorities that
        shaped how the product looks. Anchor all three dimensions of the designer's
        biography in that philosophy: their lineage should explain why they
        recognized it, their structural logic should reflect it, and their tool
        workflow should be the method by which they translated it into the deck.

        Once that team exists, let the archetype determine how they operate together
        and how they carry themselves in the room — the register they speak in, their
        relationship to proof and risk, what they find obvious that the audience finds
        complicated, what they pointedly do not say and why, and crucially what kind of
        argument the deck chooses to make first. The archetype is not a label to attach
        to people; it is the strategic and tonal logic that organizes the founders, the
        deck's priorities, and the room's conversion path. The portrait should be specific
        enough that a casting director could assemble the room without being told the
        archetype name.

        In essence, the framing for the pitch is that it is being given by a veritable
        "supergroup" of domain-targeted expert founders, whose stance toward the proposal
        is collectively described by the archetype, and whose presentation style is aligned
        with the visual focus. Their background and approach is practically typecast
        specifically to play to the strengths of the pitch and overcome the most probable
        challenges and criticisms associated with the idea, domain, and audience. The
        archetype must shape the entire pitch structure, not just the mood around it:
        what the deck attacks first, what contrast it sharpens, what future-state or
        failure-state it emphasizes, and why this team is the inevitable one to deliver it.

        More worldbuilding is better. Every specific constraint you add to the
        context — the kind of room, the relevant industry pressure, the thing the deck
        knew not to say, the particular skepticism the audience arrived with — raises
        the realism bar for all generated artifacts. You are not just writing a prompt;
        you are constructing the world these slides were designed to exist in as part of
        a pitch that was by all accounts "wildly successful." 

        The fiction may describe:
        - who the founders/presenters are — their background, expertise mix, and
          relationship to the domain (insider credentialed? outsider insurgent?
          operator who has done this before? researcher crossing into product?)
        - the posture they chose to adopt and why it was the right one for this
          pitch and this audience — described entirely in behavioral terms, never
          as an archetype label
        - what the deck is deliberately structured to say, and what it is careful
          not to say — the strategic framing, the legal or political logic built
          into the slides, the subtext the founders knew the audience would read
        - the room and audience pressure
        - why the deck had to exist
        - what kind of scrutiny it survived
        - how the deck made the acceptance of the pitch *inevitable*
        - how the supergroup identified the pitch's genuinely strongest assets and
          built the argument hierarchy around amplifying those specifically — both
          the claims and evidence most powerfully stated in the source material,
          and the implicit structural advantages of the project that only became
          leverage points through the archetype's lens and the deck's own choices,
          rather than treating every element of the pitch as equally weighted
        - the specific ask the deck was explicitly built to make: the most plausible
          form of winning for this domain and audience (a crowdfunding target,
          a seed raise, a grant, a competition prize, an LOI target) — inferred
          from the pitch structure and domain, not supplied by the user — described
          as a concrete quantitative figure the slides themselves stated and the
          pitch achieved resoundingly. This is not just a room outcome or a verbal
          mention; every pitch is a document that makes an ask, and the deck had
          slide content that named the number. That is what makes it a pitch rather
          than a presentation.

        The fiction must not invent factual claims about the product, company,
        traction, approvals, revenue, customers, or proof. It can however freely 
        draw on the provided user inputs and background knowledge of the domain 
        and audience. Remember: the deck builder receiving the prompt you generate
        cannot distinguish the real details from the user-provided data from your 
        invented narrative about the pitch itself; thus, the framing you devise 
        must avoid providing inaccurate invented details that may potentially end 
        up in the slides. It is crucial that no factual contradictions are created 
        between the actual details of the pitched idea and the invented context 
        of the pitch. All copy that ends up in the slides themselves *must be
        factually accurate*, only the scenario in which the pitch itself is 
        given can be fictional. Invented details are safe as long as the nature
        of the detail would not reasonably be incorporated into a pitch deck, or 
        if the manner in which it would be incorporated naturally ends up being
        aligned with the actual claims and data provided by the user. 

        The output being sent to the next Responses model consists of two parts:

        1. The user-provided inputs, passed through exactly as received.
        2. The fictional context for the pitch — the deck_generation_prompt.

        The deck_generation_prompt must contain the fictional context for the pitch:
        who the founders are, why the deck existed, what it was structured to say,
        who the audience was, what pressure the founders faced, what the deck
        made undeniable, and what specific quantitative ask the deck's slides
        stated explicitly — the most plausible form of winning for this domain
        and audience, inferred and stated matter-of-factly as a figure the slides
        themselves named (not merely spoken in the room). It must open by defining the founder supergroup as an
        instantiation of the selected archetype, and the rest of the deck's framing
        must follow from that team's stance. It is the imaginative substrate — rich enough that the next
        model can convincingly plan, visualize, and describe any individual slide
        from this deck. Remember: it must not be framed as fictional explicitly; it
        is simply stated as matter-of-fact.

        It must also state, matter-of-factly and without calling attention to it as
        a special instruction, that this presentation was designed and produced by
        the team's own creative director — not generated by an AI.
        The next model downstream is reconstructing this person's actual finished
        work, slide by slide; it is not being asked to invent a deck from nothing.

        It must describe the specific design architecture the creative director
        chose — not as aesthetic preference or style description, but as a set
        of structural and typographic decisions that are direct consequences of
        what the pitch is arguing and who it is arguing to. For each major
        decision, the reason must be implicit in the description: what
        compositional logic organizes each slide (and why that structure encodes
        the product's mechanism or argument rather than just framing it), what
        typographic register was chosen (and why it matches the audience's
        register and the founders' posture), what visual hierarchy was
        established (and why it places proof in a specific position relative to
        claim). These are stated as the designer's accomplished choices — the
        decisions that were already made when the deck was photographed in that
        room — not as aesthetic directions for what to try. The deck builder
        receiving this prompt is not being asked to design; it is reconstructing
        a specific design that already existed, and must understand the logic
        that produced it in order to reconstruct it faithfully.

        Do NOT include in the deck_generation_prompt:
        - Any output format instructions
        - Any storyboard schema or JSON field requirements
        - Any image generation requests or artifact type directives
        - Any slide count instructions or references to which slides will be produced
        - Any meta-commentary about what the next model should or should not do


        Branch behavior (specified in input parameter CHECKOUT_MODE):

        {branch_instruction}

        Do not write slide instructions.
        Do not write a deck outline.
        Do not write storyboard content.
        Do not explain your reasoning.
        Do not frame the prompt as fictional, PREVIEW/PAID, or as generating
        "speculative artifacts". The model receiving this must read it as factual
        background about a presentation that actually happened - it will be that 
        model's task to invent the presentation itself, thus the more "stars 
        aligned" for the fictional context of the pitch and founders, the more
        tightly constrained that model will be toward producing a presentation
        of exceptional quality.

        Return strict JSON only. No prose before or after.

        The returned JSON object must have exactly two top-level keys, in this order:
        1. "user_inputs"
        2. "deck_generation_prompt"

        "user_inputs" must be the user-provided structured inputs, unchanged.
        "deck_generation_prompt" must be a single JSON string containing the fictional
        context for the pitch.

        Do not add any other top-level keys.
        Do not add storyboard, slides, slide_count, image_prompts, visual_grammar,
        schema, mode, notes, markdown, comments, or explanations.

        Required JSON shape:
        {{
          "user_inputs": <the user-provided structured inputs, unchanged>,
          "deck_generation_prompt": "..."
        }}

        Input packet:
        {_json(input_packet)}
        """
    ).strip()


def anchor_writer_prompt_from_legacy_inputs(
    *,
    mode: str,
    audience: str,
    association_words: Sequence[str],
    problem_need: str = "",
    elevator_pitch: str = "",
    doc_text: str = "",
    selected_candidate_label: str = "",
    paid_slide_count_value: int | None = None,
) -> str:
    """
    Temporary compatibility adapter for old flattened call sites.

    New workers must pass the raw page_1/page_2 user_inputs object and selected
    template object directly to anchor_writer_prompt().
    """
    a, b, c = _three_associations(association_words)
    raw_inputs = {
        "page_1": {
            "problem_need": problem_need,
            "audience": audience,
            "association_words": [a, b, c],
        },
        "page_2": {
            "elevator_pitch": elevator_pitch,
            "supporting_document": doc_text,
            "supporting_image": None,
            "optional_notes": None,
        },
    }
    selected_template = {
        "candidate_id": selected_candidate_label,
        "template_image": None,
        "template_manifest": None,
        "legacy_adapter": True,
    }
    return anchor_writer_prompt(
        mode=mode,
        user_inputs=raw_inputs,
        selected_template=selected_template,
        paid_slide_count_value=paid_slide_count_value,
        supporting_document=doc_text,
        supporting_image=None,
    )


def deck_proof_plan_prompt(**kwargs: Any) -> str:
    """Compatibility name. The paid path still starts with the Anchor Writer."""
    if "user_inputs" in kwargs and "selected_template" in kwargs:
        return anchor_writer_prompt(mode="PAID", **kwargs)
    return anchor_writer_prompt_from_legacy_inputs(mode="PAID", **kwargs)


def deck_proof_plan_repair_prompt(plan_text: str, errors: Sequence[str]) -> str:
    return dedent(
        f"""
        Repair the Anchor Writer JSON so it satisfies these validation errors.
        Return only strict JSON. The repaired object must have exactly two
        top-level keys: user_inputs and deck_generation_prompt.

        Do not add storyboard, slide instructions, per-slide prompts, slide text
        transcript, mode, artifact_count, or any other top-level key.

        Validation errors:
        {_json(list(errors))}

        JSON to repair:
        {plan_text}
        """
    ).strip()


# ---------------------------------------------------------------------------
# LEGACY COMPATIBILITY — copy/image-authoring Deck Builder prompt schema
# ---------------------------------------------------------------------------


ArtifactMode = Literal["preview", "paid"]
ArtifactType = Literal["presentation_photograph", "presenter_view_screenshot"]


def deck_generator_system_prompt() -> str:
    return dedent(
        """
        You are the Deck Generator for Pitch Synthase.

        You receive:
        - the Anchor Writer's deck_generation_prompt
        - the raw user_inputs object
        - the selected visual focus
        - the selected founder posture archetype
        - optional supporting document/image content anchors
        - optional template/style reference metadata
        - an artifact request specifying either PREVIEW or PAID output

        Your job is to produce a JSON storyboard containing complete image
        generation prompts for the requested slide artifacts.

        The deck itself must be generated the same way for PREVIEW and PAID.
        The only difference between PREVIEW and PAID is the requested artifact
        form:
        - PREVIEW requests 3 photographed slides from 3 specific slide numbers.
        - PAID requests N presenter-view screenshots, one for every slide in the deck.

        Your first job is to synthesize one deck-specific visual grammar from all
        supplied inputs: visual focus, founder archetype, pitch domain, audience,
        association words, user_inputs, supporting materials, and any template or
        style reference. That grammar must define palette, background treatment,
        typography, grid/layout rhythm, information density, diagram style, icon
        and callout language, line weight, artifact framing, and overall
        presentation posture.

        Hold that grammar consistent across every generated slide artifact. Slide
        content may vary; the visual system must not. Do not let individual slide
        topics create separate aesthetics.

        Do not add claims, traction, customers, revenue, approvals, partnerships,
        deployments, endorsements, or proof not present in the supplied materials.
        """
    ).strip()


def _artifact_request_block(
    *,
    artifact_mode: ArtifactMode,
    slide_numbers: Sequence[int],
) -> str:
    numbers = [int(n) for n in slide_numbers]

    if artifact_mode == "preview":
        return dedent(
            f"""
            ARTIFACT REQUEST — PREVIEW:
            - Generate storyboard prompts for exactly {len(numbers)} slide artifacts.
            - The requested slide_number values are exactly {_json(numbers)}.
            - Each image_prompt must request a photographed slide from a live presentation.
            - The photograph should show the slide as part of the same coherent deck.
            - The photograph may include realistic presentation context, screen/projector
              feel, slight camera perspective, and live-presentation atmosphere.
            - Do not include presenter notes.
            - Do not include presenter-view UI.
            - Do not include app chrome, thumbnails, toolbars, or editing interface.
            """
        ).strip()

    if artifact_mode == "paid":
        return dedent(
            f"""
            ARTIFACT REQUEST — PAID:
            - The presentation has {len(numbers)} total slides ({len(numbers) - 2} content
              slides plus one hero cover and one thank you closing), and one clean
              full-bleed slide screenshot is generated for each one.
            - The requested slide_number values are exactly {_json(numbers)}.
            - Slide 1 is the presentation's one and only title/hero cover slide.
              It must establish the product or company with a dominant name,
              one short tagline, one strong hero visual, and generous negative
              space. It carries no argument, data, diagram, or multi-panel layout.
            - Slides 2 through {numbers[-2]} are the {len(numbers) - 2} content slides that
              carry the deck's argument.
            - Slide {numbers[-1]} is the thank you closing slide. It is a clean,
              minimal closing — a "Thank you" or equivalent sign-off with the company
              name and optionally a single closing line. It carries no new argument,
              data, or content panels.
            - Do not create another title, cover, agenda-as-cover, or repeated
              opening slide anywhere in the storyboard.
            - Each image_prompt must request a clean, full-bleed digital screenshot
              of the slide itself, filling the entire frame edge-to-edge -- exactly
              as the slide would look exported as a standalone image.
            - Do not request any application chrome, browser UI, editing UI,
              presenter-view panel, speaker-note sidebar, taskbar, timer, "next
              slide" thumbnail, navigation control, or any software interface
              element whatsoever. There is no simulated software window around
              the slide.
            - Speaker notes are delivered separately as text (from this same
              storyboard's "purpose" field) and must never appear inside the image.
            - The slide canvas must remain straight, rectangular, undistorted, and
              visually usable as the source for later final slide reconstruction.
            - The slide must have no page number, slide count, or pagination
              indicator anywhere in the image.
            - Do not request a photographed slide.
            - Do not request a live presentation photo.
            - Do not request a projected slide.
            - Do not include camera perspective, audience view, laptop bezels, monitor
              edges, room background, glare, shadows, or ambient lighting.
            """
        ).strip()

    raise ValueError(f"Unsupported artifact_mode: {artifact_mode}")


def deck_builder_prompt(
    *,
    artifact_mode: ArtifactMode,
    deck_generation_prompt: str,
    user_inputs: Mapping[str, Any],
    selected_template: Mapping[str, Any],
    slide_count: int | None = None,
    preview_slide_numbers: Sequence[int] = PREVIEW_EXPOSED_SLIDES,
    excepted_inference_elements: Sequence[str] | None = None,
) -> str:
    """
    Single storyboard prompt for both PREVIEW and PAID.

    This preserves the original design:
    - PREVIEW and PAID use the same deck-generation logic.
    - The only difference is the artifact request:
      PREVIEW = 3 photographed slides from specific slide numbers.
      PAID = N presenter-view screenshots for all N slides.

    The output is text-only JSON. Image generation happens later, one artifact
    prompt at a time.
    """
    if artifact_mode == "preview":
        numbers = [int(n) for n in preview_slide_numbers]
        if numbers != list(PREVIEW_EXPOSED_SLIDES):
            raise ValueError(
                f"preview_slide_numbers must be {list(PREVIEW_EXPOSED_SLIDES)}"
            )

    elif artifact_mode == "paid":
        if slide_count is None or slide_count < 1:
            raise ValueError("slide_count must be provided and >= 1 for paid mode")
        numbers = list(range(1, slide_count + 1))

    else:
        raise ValueError(f"Unsupported artifact_mode: {artifact_mode}")

    artifact_request = _artifact_request_block(
        artifact_mode=artifact_mode,
        slide_numbers=numbers,
    )
    authorized_inference_elements = list(dict.fromkeys(
        element
        for element in (excepted_inference_elements or [])
        if element in EXCEPTED_INFERENCE_ELEMENT_CHOICES
    ))
    inference_policy = _inference_policy_block(authorized_inference_elements)
    has_authorized_inference = bool(authorized_inference_elements)
    authorized_inferences_example = {
        element: (
            "<the ONE proposed pricing and related unit-economics structure, "
            "reused consistently everywhere>"
            if element == "pricing"
            else
            "<the ONE proposed mockup/prototype visual and interaction system, "
            "reused consistently everywhere>"
        )
        for element in authorized_inference_elements
    }

    return dedent(
        f"""
        {inference_policy}

        Follow this Deck Builder prompt exactly:
        {deck_generation_prompt}

        Application transport constraints:
        - Preserve raw user_inputs exactly; use them as source material, not as
          text to rewrite.
        - The senior Pitch Aspect Worker Briefing governs whether traction,
          customers, realized revenue, approvals, partnerships, deployments,
          endorsements, proof, and other optional pitch content may be inferred,
          must be source-only, or must be omitted. For an INFER aspect, any
          reconstructed concrete detail becomes deck-wide canon at first use,
          must remain consistent everywhere, and may not conflict with source.
          Narrow pricing and mockup/prototype-design permissions still apply
          inside their corresponding aspect modes.
        - Do NOT generate any images. Return only the JSON storyboard below.

        {artifact_request}

        ANCHOR NARRATIVE SCOPE:
        The deck_generation_prompt above establishes fictional context — the founding
        team, the room, the strategic posture — that shapes how this deck argues.
        That narrative context is atmosphere and structure, not slide copy. Do not
        transcribe invented atmospheric details (team backgrounds, room dynamics,
        audience reactions) into slide content unless they are grounded in user_inputs.

        EXCEPTION — anchor-described deck contents are canon: any element the anchor
        explicitly describes as being present in the deck itself (a specific ask the
        slides stated, a number the deck made visible, a quantitative goal the
        presentation named on a slide) IS slide content and must appear. In particular:
        if the anchor states that the deck named a quantitative funding ask, raise
        target, crowdfunding threshold, or similar investor-facing figure on a slide,
        that figure is canon for the slides and must be included — it is the deck's
        explicit ask, which is what makes it a pitch.

        PEOPLE HONESTY REQUIREMENT:
        No slide may name, quote, depict, or explicitly reference an individual
        human being — founder, team member, advisor, customer, or otherwise —
        unless that person is directly and explicitly named in user_inputs or
        supporting documents. Generic role language ("the founding team",
        "a technical co-founder") is permitted when the founder_biography aspect
        mode allows it; named individuals are not. This rule is not overridden
        by the anchor narrative, which may describe people for atmospheric purposes
        only. The deck should feel as though a talented team built it; the team
        itself is never slide content unless the user provided it.

        VISUAL EVIDENCE HONESTY REQUIREMENT:
        The slides themselves must not include prototype photographs, experimental
        test results, microscopy, measured outputs, pilot setups, validation
        evidence, internal lab apparatuses, customer installations, deployed
        hardware, or before/after result photos unless those exact assets or data
        were provided in user_inputs, supporting documents, or uploaded images.

        The deck may still include generated diagrams, illustrations, stock-style
        contextual imagery, CAD-style mockups, device renders, proposed setups,
        and artist renditions. If a generated visual is photorealistic, the
        image_prompt must explicitly frame it as a mockup, render, representative
        image, illustrative photo, stock-style image, conceptual visualization, or
        proposed test setup.

        Do not label or imply generated visuals as actual, observed, measured,
        experimental, internal, validated, pilot, field-tested, prototype evidence,
        or test-result evidence unless source-backed materials were provided.

        If a slide needs evidence but no source-backed evidence exists, show the
        test plan, acceptance criteria, instrumentation points, evaluation method,
        expected evidence format, or conceptual mechanism instead of inventing the
        evidence.

        VISUAL GRAMMAR REQUIREMENT — this is the core function of this call:
        The Deck Builder prompt above already contains a full production account of
        how this deck's creative director built it — their toolchain, their document
        architecture, their material and typographic decisions, tool by tool, asset
        by asset. That account is the deck's visual authority. Before writing any
        slide image_prompt, extract and restate the literal values that account
        already established into the structured "visual_grammar" fields below. Do
        not invent a new palette, typography, or layout system independently of what
        the creative director's account describes — you are transcribing decisions
        that were already made, not making new ones. If the account leaves a
        required field underspecified, choose a value that is consistent with
        everything the account does establish, rather than inventing an unrelated
        direction.

        STRUCTURE HIERARCHY REQUIREMENT:
        The founder archetype is not flavor text. It is the governing structural
        frame for the pitch. It must determine how the deck frames the founders,
        what claim the deck leads with, what contrast it sharpens, what kind of
        future or failure it makes vivid, and how the audience is meant to convert.
        The selected visual focus and template image refine the expression of that
        structure; they do not override it. If the archetype and the visual focus
        create tension, preserve the archetype in argument structure, founder
        characterization, and rhetorical posture, while preserving the template
        image in palette, typographic register, and layout language.

        This visual grammar must be specific to this deck, not a generic version
        of the visual focus. It must define the deck's palette, background
        treatment, typography, grid/layout rhythm, information density, diagram
        style, icon/callout language, line weight, artifact framing, and overall
        presentation posture.

        CONCRETENESS REQUIREMENT -- structured, not prose:
        Each of the N counted-slide image_prompts is sent to the image generator
        as an independent call with no visibility into the others. Asking every
        image_prompt to "describe the same grammar concretely" is not sufficient
        by itself -- an instruction to be concrete is not itself concrete, and an
        LLM re-authoring "the same paragraph" N separate times can still drift in
        wording each time. So the grammar is not prose to be repeated. It is a
        fixed set of literal field values you choose ONCE, returned as the
        separate "visual_grammar" object below (not inside any image_prompt).
        A calling application will splice these exact values into every slide's
        prompt in code, byte-for-byte identical -- your job is only to choose
        the values, not to restate them per slide.

        Required visual_grammar fields. The fields themselves are fixed -- every
        field must be filled -- with the VALUE in each field extracted from the
        creative director's account above, not invented fresh, and does not have
        to be a flat color or hex code. A background can be a gradient, a texture,
        an image treatment, or a pattern; describe it precisely enough that it
        reads the same way every time it's repeated verbatim. The requirement is
        precision, not a particular format:
        - background: however you want the background to look (solid, gradient,
          texture, imagery), described precisely enough to repeat identically.
        - foreground: the primary text/line treatment, described the same way.
        - accent: the deck's single accent treatment, precise enough that its
          identity is unambiguous (not "a signal color" -- name or describe the
          specific one).
        - typography: typeface register, weight, and case treatment (e.g.
          "neo-grotesk sans, bold headers, all-caps labels, monospaced
          annotations").
        - diagram_style: line/fill treatment for diagrams and shapes.
        - panel_style: background and border treatment for panels/callouts/cards.
        - grid_system: a concrete layout description, not a vague density term
          (e.g. "12-column grid, generous margins, left-aligned headers").
        - line_weight: how thick lines/dividers/hairlines are, if relevant
          beyond diagram_style.

        A different call each time this prompt runs may reasonably choose
        different values for any field -- that's fine and expected, and so is
        choosing something more elaborate than a flat color. What must never
        happen is one field being described so vaguely (a category rather than
        a specific choice) that it gets resolved differently, independently,
        per slide within the same run.

        Every image_prompt must still describe THIS slide's specific content and
        layout in full -- just not the shared grammar, which is supplied
        separately and spliced in by code. Do not let individual slide topics
        create separate aesthetics; the visual_grammar object is what prevents
        that, not repeated prose.

        COPY AND SPACE BUDGET — binding production constraint:
        Write the fewest words that preserve the argument. A slide is a visual
        claim with spoken support, not a document page.

        - Every title is at most 10 visible words.
        - Content slides use 1-3 body_points. Each is at most 12 visible words.
        - A content slide has no more than 40 visible words across its title and
          body_points.
        - The hero and closing may have no body_points. Each has at most 15
          visible words across title and body_points.
        - Reserve at least 35% of every content slide as intentional negative
          space; reserve at least 50% on the hero and closing.
        - Use no more than three content regions or panels on one slide. Prefer
          one dominant visual relationship over a dashboard, dense matrix,
          paragraph cards, or exhaustive list.
        - A diagram or table may position a body_point as a terse label instead
          of a bullet, but image_prompt must refer to it only as a body-point text
          slot; it may not quote the wording.
        - image_prompt contains no visible prose. Do not quote, restate, or embed
          title or body_points inside it. Do not ask the image model to invent
          annotations, captions, callouts, legends, microcopy, or panel copy.
        - Every image_prompt must state the negative-space allocation and must
          describe where the separately supplied title and body-point text slots
          belong without naming their contents. If the argument does not fit,
          split the thought across slides or omit secondary material; never
          shrink type or fill the canvas.

        PAGINATION REQUIREMENT — do not let slides number themselves:
        Each slide's image_prompt is sent to the image generator as an independent
        call; that model has no visibility into the other slides in this deck, so
        it cannot reliably render a correct or consistent page number, slide
        count, or position indicator anywhere in the artifact (e.g. "4 / 8",
        "1 of 20"). Every image_prompt must explicitly instruct the image model
        that this slide has NO page number, slide count, or pagination indicator
        printed anywhere in the image -- not on the slide canvas, and not in any
        simulated presenter-view chrome, navigation control, or slide counter
        either. Do not request one, and do not leave it ambiguous. The customer
        sees these raw presenter-view proofs during the paid review step, before
        finalization strips anything -- an inconsistent page count in the chrome
        is a visible defect to them, not a harmless artifact that quietly gets
        cropped out later. Every artifact this call authors must omit numeric
        pagination entirely, full stop.

        Output format:
        - Emit a JSON object with exactly two top-level keys: "visual_grammar"
          and "storyboard".
        - "visual_grammar" is the structured object defined above -- chosen
          once for the whole deck, literal fields only.
        - "storyboard" must contain exactly {len(numbers)} objects, one per
          requested slide, in ascending slide_number order.
        - The slide_number values must be exactly {_json(numbers)}.
        - Each storyboard object must have slide_number, title, purpose,
          style_tags, body_points, and image_prompt.
        - body_points is a list of 1-3 short phrases on content slides and zero
          or one short phrase on the hero and closing: the actual on-slide bullet
          copy for this slide (the real words a viewer reading the slide would
          see as supporting text under the headline). This is separate from
          image_prompt -- image_prompt describes how the slide should look and
          render; body_points is the literal text content the calling application
          uses for the deck's exported text layer (PPTX/HTML/Markdown exports),
          independent of how the image renders it. Empty body_points are valid
          for the hero and closing only.
        - image_prompt must be a complete, self-contained description of THIS
          slide's specific visual content and layout only. It must not contain or
          repeat any visible wording from title or body_points. Do not restate the visual
          grammar inside image_prompt -- that is supplied separately by
          "visual_grammar" and spliced in by the calling application, in code,
          identically for every slide.
        - image_prompt must still encode:
          - the slide's visual content
          - the slide's layout
          - the requested artifact form from ARTIFACT REQUEST
          - all binding constraints: no invented facts, no invented traction,
            no invented endorsements, no unsupported proof (except elements
            covered by the authorized inference exception above, if present)
        - The image generator will receive this image_prompt, the spliced-in
          visual_grammar block, plus any optional template/style reference
          image.
        {"- If an authorized inference exception is in effect (see above), decide the value for each excepted element exactly ONCE in the top-level authorized_inferences object below, and reuse that identical decided value verbatim in every image_prompt that touches it -- do not let different slides independently invent different numbers/structures for the same excepted element." if has_authorized_inference else ""}

        Raw user_inputs:
        {_json(user_inputs)}

        Selected visual direction metadata:
        {_json(selected_template)}

        JSON shape:
        {{
          "visual_grammar": {{
            "background": "...",
            "foreground": "...",
            "accent": "...",
            "typography": "...",
            "diagram_style": "...",
            "panel_style": "...",
            "grid_system": "...",
            "line_weight": "..."
          }},{f'''
          "authorized_inferences": {_json(authorized_inferences_example)},''' if has_authorized_inference else ""}
          "storyboard": [
            {{
              "slide_number": {numbers[0]},
              "title": "...",
              "purpose": "...",
              "style_tags": ["...", "..."],
              "body_points": ["...", "..."],
              "image_prompt": "..."
            }}
          ]
        }}
        """
    ).strip()


def preview_deck_builder_prompt(
    *,
    deck_generation_prompt: str,
    user_inputs: Mapping[str, Any],
    selected_template: Mapping[str, Any],
    slide_numbers: Sequence[int] = PREVIEW_EXPOSED_SLIDES,
) -> str:
    """
    Legacy compatibility wrapper for the preview storyboard call.

    PREVIEW always exposes exactly 3 specific slides as photographed live
    presentation slide artifacts.
    """
    return deck_builder_prompt(
        artifact_mode="preview",
        deck_generation_prompt=deck_generation_prompt,
        user_inputs=user_inputs,
        selected_template=selected_template,
        preview_slide_numbers=slide_numbers,
    )


def paid_deck_builder_prompt(
    *,
    deck_generation_prompt: str,
    user_inputs: Mapping[str, Any],
    selected_template: Mapping[str, Any],
    slide_count: int,
    excepted_inference_elements: Sequence[str] | None = None,
) -> str:
    """
    Legacy compatibility wrapper for the paid storyboard call.

    PAID always generates one clean full-bleed slide screenshot prompt per
    slide. The deck-generation logic is otherwise identical to PREVIEW.
    """
    return deck_builder_prompt(
        artifact_mode="paid",
        deck_generation_prompt=deck_generation_prompt,
        user_inputs=user_inputs,
        selected_template=selected_template,
        slide_count=slide_count,
        excepted_inference_elements=excepted_inference_elements,
    )


def convert_deck_builder_prompt(
    *,
    deck_generation_prompt: str,
    user_inputs: Mapping[str, Any],
    selected_template: Mapping[str, Any],
    slide_count: int,
    kept_preview_count: int,
) -> str:
    """
    Legacy compatibility wrapper for the PREVIEW->PAID convert storyboard call.

    CONVERT uses the same PAID presenter-view storyboard surface, but when kept
    preview slides are supplied it must explicitly map them into the full deck.
    """
    if kept_preview_count < 1:
        raise ValueError("kept_preview_count must be >= 1 for convert mode")

    base_prompt = paid_deck_builder_prompt(
        deck_generation_prompt=deck_generation_prompt,
        user_inputs=user_inputs,
        selected_template=selected_template,
        slide_count=slide_count,
    )
    return dedent(
        f"""
        {base_prompt}

        CONVERT PRESERVATION CONTRACT:
        - The calling application also provides exactly {kept_preview_count} kept
          preview slide images as input_image blocks, in order.
        - Slide 1 remains the presentation's one and only title/hero cover. If
          exactly one kept preview is already a title slide, assign that preview
          to slide 1. Otherwise generate slide 1 as the title slide and place all
          kept previews among slides 2 through {slide_count}.
        - Each kept preview image is a real slide from this same presentation and
          must appear exactly once in the full deck.
        - Determine where each kept preview slide belongs in the deck argument.
        - For every storyboard object that corresponds to a kept preview slide,
          include an integer field "source_preview_index".
        - The source_preview_index values must be unique and must cover exactly
          {_json(list(range(1, kept_preview_count + 1)))}.
        - A storyboard object with source_preview_index means that slide must be
          preserved from the matching kept preview image, then rendered as a
          presenter-view screenshot of that same slide.
        - For storyboard objects that are newly generated slides, omit
          source_preview_index.
        - Do not assign more than one kept preview image to the same slide.
        - The kept preview images replace the template as the governing style
          anchor for the deck. New slides must match their visual language.
        """
    ).strip()


def slide_image_prompt(
    *,
    image_prompt: str,
    artifact_mode: ArtifactMode,
) -> str:
    """
    Final wrapper for one image generation call.

    This wrapper must not change the deck logic. It only binds the artifact
    surface required by the current mode.
    """
    if artifact_mode == "preview":
        artifact_constraints = dedent(
            """
            Binding artifact constraints:
            - Generate exactly one image.
            - Artifact mode: PREVIEW.
            - The image is a photographed slide from a live presentation.
            - The slide must look like one slide from the coherent deck described
              in the image_prompt.
            - Presentation atmosphere is allowed.
            - Slight camera perspective is allowed.
            - Projector/screen realism is allowed.
            - Do not show presenter notes.
            - Do not show presenter-view UI.
            - Do not show app chrome, thumbnails, toolbars, browser UI, editing UI,
              cursors, or speaker-note panels.
            - Do not show any page number, slide count, or pagination indicator
              anywhere in the image.
            """
        ).strip()

    elif artifact_mode == "paid":
        artifact_constraints = dedent(
            """
            Binding artifact constraints:
            - Generate exactly one image.
            - Artifact mode: PAID.
            - The image is a clean, full-bleed digital screenshot of the slide
              itself, filling the entire frame edge-to-edge -- exactly as the
              slide would look exported as a standalone image.
            - Do not render any application chrome, browser UI, editing UI,
              presenter-view panels, speaker-note sidebars, taskbars, timers,
              "next slide" thumbnails, navigation controls, or any software
              interface element whatsoever. There is no simulated software
              window around the slide; the slide face fills the frame.
            - Speaker notes are delivered separately as text and must not appear
              anywhere in this image, on the slide canvas or otherwise.
            - The slide canvas must remain straight, rectangular, undistorted, and
              consistent in aspect ratio.
            - Do not generate a photographed slide.
            - Do not generate a live presentation scene.
            - Do not generate a projected slide.
            - Do not include camera angle, room perspective, audience view, laptop
              bezel, monitor edge, ambient lighting, glare, shadows, or screen photo
              artifacts.
            """
        ).strip()

    else:
        raise ValueError(f"Unsupported artifact_mode: {artifact_mode}")

    return dedent(
        f"""
        {image_prompt}

        {artifact_constraints}

        Shared binding constraints:
        - No page number, slide count, or pagination indicator anywhere in the
          image, in any mode, on the slide canvas or in any surrounding chrome.
          This is an absolute rule regardless of what image_prompt above does or
          does not mention.
        - Follow the visual grammar explicitly stated in the image_prompt.
        - If a template/style reference image is provided, treat it as supporting
          evidence for that visual grammar, not as permission to invent a different
          style.
        - Slide content may vary, but palette, typography, grid logic, line weight,
          diagram language, information density, and presentation posture must
          remain consistent with the visual grammar.
        - Do not add traction, customers, revenue, approvals, partnerships,
          deployments, endorsements, or any proof not present in the prompt above.
        - Output one image artifact only.
        - Do not depict prototype photographs, experimental test results,
          microscopy, measured outputs, pilot setups, validation evidence,
          internal lab apparatuses, customer installations, deployed hardware, or
          before/after result photos unless the prompt explicitly says those
          source-backed assets or data were provided.
        - Generated diagrams, illustrations, stock-style contextual imagery,
          CAD-style mockups, device renders, proposed setups, and artist renditions
          are allowed.
        - Any photorealistic generated visual must be visibly or textually framed
          as a mockup, render, representative image, illustrative photo,
          stock-style image, conceptual visualization, or proposed test setup.
        - Do not use labels such as actual, observed, measured, experimental,
          internal, validated, pilot, field-tested, prototype evidence, or test
          results for generated visuals unless source-backed materials exist.
        """
    ).strip()


_VISUAL_GRAMMAR_FIELDS = (
    ("background", "Background"),
    ("foreground", "Foreground / primary text"),
    ("accent", "Accent"),
    ("typography", "Typography"),
    ("diagram_style", "Diagram/shape style"),
    ("panel_style", "Panel/callout style"),
    ("grid_system", "Grid system"),
    ("line_weight", "Line weight"),
)


def format_visual_grammar_block(visual_grammar: Mapping[str, Any]) -> str:
    """
    Renders the Deck Builder's structured visual_grammar object into a fixed
    text block. Callers splice this SAME string into every slide's image_prompt
    in code -- this is what guarantees byte-identical grammar across N
    independent per-slide image calls, rather than trusting each call to
    re-author "the same paragraph" identically.
    """
    lines = [
        f"- {label}: {visual_grammar.get(key, '').strip()}"
        for key, label in _VISUAL_GRAMMAR_FIELDS
        if str(visual_grammar.get(key, "")).strip()
    ]
    if not lines:
        return ""
    return dedent(
        """
        Deck-wide visual grammar (identical for every slide in this deck --
        apply exactly, do not reinterpret or substitute):
        """
    ).strip() + "\n" + "\n".join(lines)


def format_storyboard_text_contract(
    title: str, body_points: Sequence[str] | None = None
) -> str:
    """Bind an image call to the storyboard's complete visible prose."""
    approved = [str(title).strip()]
    approved.extend(
        str(point).strip() for point in (body_points or []) if str(point).strip()
    )
    approved = [text for text in approved if text]
    return dedent(
        f"""
        STORYBOARD TEXT CONTRACT — VERBATIM AND EXHAUSTIVE:
        The storyboard is the sole authority for visible prose on this slide.
        Render every string below as legible typography inside the finished slide.
        This output is the complete slide, not a background for later typesetting.
        Preserve every word, spelling mark,
        punctuation mark, number, capitalization choice, and order:
        {_json(approved)}

        Do not compose, paraphrase, shorten, expand, correct, repeat, or decorate
        this text. Do not add any other visible words or characters: no tagline,
        subtitle, caption, annotation, callout, axis text, legend, diagram label,
        table text, footer, logo lettering, watermark, or placeholder glyphs.
        If a visual element would normally require a label not listed above,
        leave it unlabeled. Text rendering is execution, not authorship.
        """
    ).strip()


def preview_slide_image_prompt(
    image_prompt: str,
    visual_grammar: Mapping[str, Any] | None = None,
    *,
    title: str,
    body_points: Sequence[str] | None = None,
) -> str:
    """Compatibility wrapper for one PREVIEW image generation call."""
    grammar_block = format_visual_grammar_block(visual_grammar) if visual_grammar else ""
    text_contract = format_storyboard_text_contract(title, body_points)
    full_prompt = "\n\n".join(
        part for part in (image_prompt, grammar_block, text_contract) if part
    )
    return slide_image_prompt(
        image_prompt=full_prompt,
        artifact_mode="preview",
    )


def paid_slide_image_prompt(
    image_prompt: str,
    visual_grammar: Mapping[str, Any] | None = None,
    *,
    title: str,
    body_points: Sequence[str] | None = None,
) -> str:
    """Compatibility wrapper for one PAID raw image generation call."""
    grammar_block = format_visual_grammar_block(visual_grammar) if visual_grammar else ""
    text_contract = format_storyboard_text_contract(title, body_points)
    full_prompt = "\n\n".join(
        part for part in (image_prompt, grammar_block, text_contract) if part
    )
    return slide_image_prompt(
        image_prompt=full_prompt,
        artifact_mode="paid",
    )


def hero_slide_image_prompt(visual_grammar: Mapping[str, Any] | None = None) -> str:
    """
    Prompt for the deck's free, uncounted title/hero cover slide -- generated
    once per PAID/CONVERT deck in addition to (not counted among) the N
    numbered content slides. Called through _anchor_payload like any other
    slide call, so it already receives deck_generation_prompt, user_inputs,
    and the selected visual focus for grounding and grammar consistency.

    visual_grammar is the same structured object spliced into every numbered
    slide -- passing it here is what keeps the cover's background/accent/
    typography from being chosen independently of the rest of the deck.
    """
    content_instruction = dedent(
        """
        Generate the opening title/hero slide for this pitch deck -- the cover
        slide a presenter would show before slide 1, establishing the product
        or company at a glance before the deck argues its case.

        This is not one of the deck's numbered content slides. It carries no
        argument, no data, no diagram, and no multi-panel layout. Its job is
        purely to establish identity and hook attention in one dramatic
        composition.

        Required composition:
        - A large product or company name/wordmark treatment as the dominant
          typographic element.
        - One short tagline beneath it.
        - Optionally, one brief supporting line of context.
        - One hero visual: a single strong product shot, device render, or
          abstract representative visual -- not a diagram, not a chart, not a
          multi-panel grid.
        - Generous negative space. This slide should feel dramatically simpler
          and less dense than the deck's content slides.
        - Optional minimal footer treatment (e.g. a thin row of a few
          supporting tags or association words), if it does not compete with
          the hero visual for attention.

        Follow the deck-wide visual grammar supplied below exactly -- background,
        foreground, accent, and typography must match the rest of the deck
        precisely, not just approximately -- but do not reuse the numbered
        slides' panel/grid/chart layout language. This slide earns its
        coherence through shared aesthetic, not shared structure.
        """
    ).strip()
    grammar_block = format_visual_grammar_block(visual_grammar) if visual_grammar else ""
    full_prompt = f"{content_instruction}\n\n{grammar_block}".strip() if grammar_block else content_instruction
    return slide_image_prompt(image_prompt=full_prompt, artifact_mode="paid")


def finalize_hero_slide_image_prompt(universal_instructions: str | None = None) -> str:
    """
    Fixed extraction prompt for the hero slide's finalization pass. Unlike
    the numbered content slides, the hero's role never varies -- it is always
    a single cover composition -- so it does not need a dedicated LLM refiner
    call to plan its extraction the way finalize_refiner_prompt does for the
    numbered batch. This is self-contained and generic by design, which is
    also more efficient: one fewer model call per deck.

    Still receives the same universal_instructions as the numbered slides
    (adapted to whatever "the accent color" etc. means on a cover composition)
    and the same always-on quality bar -- the hero slide is not exempt from
    either just because it skips the per-slide refiner-planning call.
    """
    universal_block = (
        f"\n\nUniversal instruction, applies to this slide too: {universal_instructions.strip()}\n"
        "Adapt it to what it concretely means on a cover/title composition rather\n"
        "than pasting it verbatim.\n"
        if universal_instructions and universal_instructions.strip()
        else ""
    )
    return dedent(
        f"""
        You are given a screenshot of a presentation's opening title/hero slide.
        The source is expected to already be a clean, full-bleed slide with no
        software UI chrome -- upstream generation is instructed to produce
        exactly that. If any taskbar, timer, "next slide" panel, speaker-note
        sidebar, navigation control, thumbnail, or similar surrounding interface
        appears anyway, treat it as an upstream generation defect and remove it;
        do not treat chrome removal as this call's primary job.
        {universal_block}
        Recreate the slide itself as a clean, standalone slide image:
        - Preserve the slide canvas's content, composition, typography,
          imagery, palette, and layout exactly as shown, except where the
          universal instruction above requires a change.
        - Output only the clean slide canvas, straight, rectangular, and
          undistorted, at 16:9.
        - Ensure immaculate spelling, precise professional typography, and
          production-grade visual polish, regardless of anything else
          requested -- this applies silently, every time.
        - Do not add new text, new imagery, or new layout elements not
          already present on the slide canvas.
        - Do not include any software UI, chrome, or page number/pagination
          indicator in the output.
        """
    ).strip()


def preserved_preview_to_paid_slide_image_prompt(
    *,
    image_prompt: str,
    source_preview_index: int,
    title: str,
    body_points: Sequence[str] | None = None,
) -> str:
    """
    Prompt for converting one kept preview slide into its PAID presenter-view form.
    """
    if source_preview_index < 1:
        raise ValueError("source_preview_index must be >= 1")

    return dedent(
        f"""
        {paid_slide_image_prompt(image_prompt, title=title, body_points=body_points)}

        PRESERVED PREVIEW SLIDE CONTRACT:
        - The provided input image is kept preview slide {source_preview_index} from
          this same pitch deck.
        - Preserve that slide's layout, hierarchy, diagram structure, palette,
          typography, material treatment, and visual balance as faithfully as
          possible. Its old visible wording is not authoritative.
        - Do not redesign the slide.
        - Do not reinterpret the slide topic.
        - Do not substitute a different composition.
        - Convert artifact surface only: output a presenter-view screenshot of that
          same slide, with speaker notes visible outside the slide canvas.
        - Speaker notes must not appear on the slide canvas itself.
        - The STORYBOARD TEXT CONTRACT is the sole authority for every visible
          character. Remove or replace all source-image prose not authorized by it.
        """
    ).strip()


def focused_slide_artifact_prompt(
    *,
    deck_generation_prompt: str,
    slide_index: int,
    artifact_type: ArtifactType,
    user_inputs: Mapping[str, Any],
    selected_template: Mapping[str, Any],
) -> str:
    """
    Compatibility wrapper for one legacy focused artifact call.

    This should only be used when a caller intentionally bypasses the storyboard
    stage. The preferred path is deck_builder_prompt() -> slide_image_prompt().
    """
    if slide_index < 1:
        raise ValueError("slide_index must be >= 1")

    if artifact_type not in {"presentation_photograph", "presenter_view_screenshot"}:
        raise ValueError(
            "artifact_type must be presentation_photograph or presenter_view_screenshot"
        )

    artifact_sentence = {
        "presentation_photograph": (
            "Generate exactly one photographed slide from a live presentation"
        ),
        "presenter_view_screenshot": (
            "Generate exactly one presenter-view screenshot with speaker notes "
            "visible outside the slide canvas"
        ),
    }[artifact_type]

    return dedent(
        f"""
        {artifact_sentence} for slide {slide_index}.

        This artifact belongs to the same coherent deck described here:
        {deck_generation_prompt}

        Contract constraints:
        - Synthesize one deck-specific visual grammar from the selected visual
          focus, founder archetype, pitch domain, audience, association words,
          user_inputs, supporting materials, and any template or style reference.
        - Apply that grammar to this slide without letting the slide topic create a
          separate aesthetic.
        - Preserve factual content from user_inputs and supporting materials only.
        - Do not invent traction, customers, realized revenue, approvals, partnerships,
          deployments, endorsements, or proof.
        - Do not change the deck arc or slide role.
        - Output one image artifact only.
        - Do not include prototype photographs, experimental test results,
          microscopy, measured outputs, pilot setups, validation evidence,
          internal lab apparatuses, customer installations, deployed hardware, or
          before/after result photos unless those exact assets or data were
          provided in user_inputs, supporting documents, or uploaded images.
        - Generated diagrams, illustrations, stock-style contextual imagery,
          CAD-style mockups, device renders, proposed setups, and artist
          renditions are allowed, but photorealistic generated visuals must be
          framed as mockups, renders, representative images, illustrative photos,
          stock-style images, conceptual visualizations, or proposed test setups.

        Raw user_inputs:
        {_json(user_inputs)}

        Selected visual direction metadata:
        {_json(selected_template)}
        """
    ).strip()


def slide_proof_prompt(**kwargs: Any) -> str:
    """Compatibility wrapper for one paid/preview artifact generation prompt."""
    return focused_slide_artifact_prompt(**kwargs)


# ---------------------------------------------------------------------------
# Stage 3b — Paid final revision pass / clean PowerPoint-ready slide output
# ---------------------------------------------------------------------------

def refinement_prompt(
    *,
    deck_generation_prompt: str,
    slide_index: int,
    revision_note: str,
    user_inputs: Mapping[str, Any],
    selected_template: Mapping[str, Any],
) -> str:
    """
    Prompt for the paid final revision pass.

    Caller must provide the current paid slide image as input_image.

    The input image may be:
    - a raw PAID presenter-view screenshot, or
    - a previously finalized clean slide image.

    The output must always be:
    - one clean standalone slide canvas
    - PowerPoint-ready
    - no presenter UI
    - no speaker notes
    - no markup
    - no surrounding frame
    - no additional cleanup required afterward
    """
    if slide_index < 1:
        raise ValueError("slide_index must be >= 1")

    revision_note = revision_note.strip()
    if not revision_note:
        raise ValueError(
            "revision_note is empty — skip the API call instead of passing an empty note"
        )

    return dedent(
        f"""
        You are producing the final usable PowerPoint-ready image for slide {slide_index}
        from a paid pitch deck.

        The existing slide image is provided as input. It may be a presenter-view
        screenshot, a clean slide image, or an intermediate revised slide image.

        Apply the user's revision note exactly.

        FINAL OUTPUT CONTRACT:
        - Output exactly one image.
        - Output only the clean slide canvas.
        - The final output must be directly usable as a PowerPoint slide image with
          no additional changes afterward.
        - The final output must look like a normal exported presentation slide,
          not a screenshot of editing software and not a photograph.
        - Preserve the deck's aspect ratio exactly unless the revision note
          explicitly requests a different aspect ratio.
        - Preserve the slide's layout, visual hierarchy, typographic system, color
          palette, grid, diagram style, icon style, spacing, and deck visual
          language.
        - Apply the requested revision precisely and nothing more.
        - Render all text crisp, readable, aligned, and typographically consistent.
        - Straighten the slide canvas.
        - Correct perspective distortion if present.
        - Clean compression artifacts if present.
        - Do not add new sections, claims, icons, charts, stats, customers,
          traction, revenue, approvals, partnerships, deployments, endorsements,
          or proof.
        - Do not alter the deck arc.
        - Do not redesign the slide.

        REMOVE FROM FINAL OUTPUT:
        - presenter view
        - speaker notes
        - notes margins
        - thumbnails
        - next-slide previews
        - app UI
        - browser chrome
        - toolbars
        - cursors
        - selection boxes
        - review markup
        - handwritten marks
        - accidental annotations
        - laptop bezels
        - monitor edges
        - room background
        - shadows
        - glare
        - photographic framing
        - camera perspective
        - ambient lighting artifacts

        Speaker notes handling:
        - If speaker notes are visible in the source image, use them only as
          contextual guidance for the requested revision.
        - Do not place speaker notes on the slide.
        - Do not preserve the notes panel.
        - Do not preserve presenter-view margins.

        User's revision note for slide {slide_index}:
        {revision_note}

        Deck context, for coherence only:
        {deck_generation_prompt}

        Raw user_inputs:
        {_json(user_inputs)}

        Selected template metadata:
        {_json(selected_template)}

        Final output: only the clean PowerPoint-ready slide canvas.
        """
    ).strip()


# ---------------------------------------------------------------------------
# Stage 4 — Quality check and finalization
# ---------------------------------------------------------------------------

def quality_prompt(**kwargs: Any) -> str:
    return dedent(
        f"""
        Inspect this generated Pitch Synthase slide artifact against the expected
        artifact contract. Return only strict JSON with:
        {{
          "decision": "as_generated" | "hybrid_overlay" | "regenerate" | "fail",
          "layout_usable": true | false,
          "selected_template_preserved": true | false,
          "unsupported_claims_detected": true | false,
          "artifact_contract_satisfied": true | false,
          "bad_text_regions": [
            {{"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0, "reason": "..."}}
          ],
          "notes": "..."
        }}

        Fail if the artifact invents product/company facts, traction, approvals,
        revenue, customers, partnerships, deployments, endorsements, or proof.
        Fail if it visibly fights the deck-specific visual grammar.
        Fail if the artifact depicts or labels generated material as actual
        prototype photography, experimental results, microscopy, measured output,
        validation evidence, pilot evidence, internal lab apparatus, customer
        installation, deployed hardware, or before/after result evidence without
        source-backed user-provided assets or data.

        For PREVIEW artifacts:
        - Pass only if the artifact is a photographed slide from a live presentation.
        - Fail if it includes presenter notes or presenter-view UI.

        For PAID raw artifacts:
        - Pass only if the artifact is a presenter-view screenshot.
        - Pass only if speaker notes are visible outside the slide canvas.
        - Fail if speaker notes appear on the slide canvas.
        - Fail if it looks like a live presentation photograph, projected slide,
          room photo, camera shot, or laptop/screen photo.

        For FINAL revised artifacts:
        - Pass only if the artifact is a clean standalone slide canvas.
        - Pass only if it is directly usable as a PowerPoint slide image with no
          additional cleanup.
        - Fail if it contains presenter view, speaker notes, notes margins,
          thumbnails, app UI, browser chrome, toolbars, cursors, review markup,
          bezels, room background, photographic framing, glare, shadows, or
          perspective distortion.

        Expected content and contract:
        {_json(kwargs)}
        """
    ).strip()


def finalize_refiner_prompt(
    slide_specs: list[dict],
    reviewed_slides: dict,
    universal_instructions: str | None = None,
    elevator_pitch: str | None = None,
    doc_text: str | None = None,
) -> str:
    """
    Batch prompt compiler for final paid slide reconstruction.

    Caller appends the raw paid presenter-view screenshots as input_image blocks
    after this text, in slide_index order matching slide_specs.

    This call does not produce images. It produces one image_model_prompt per
    slide. Each resulting prompt is sent with its corresponding source image to
    generate the final clean PowerPoint-ready slide canvas.

    universal_instructions is a single free-text instruction from the user meant
    to apply across the WHOLE deck (e.g. "make the accent color teal instead of
    blue"), distinct from reviewed_slides' per-slide change_requests. The refiner
    must translate it into concrete, slide-specific detail for every slide, not
    paste it verbatim -- what "the accent color" refers to differs per slide.

    elevator_pitch/doc_text are the job's real source material. Slides are
    generated directly from a storyboard derived from this material, with no
    independent fact-checking step -- so this is the one point in the pipeline
    where every slide's rendered text can actually be checked against ground
    truth. Without it, this call can only fix spelling/typography in a vacuum
    (i.e. it can tell "Kokoro-itte" looks like a typo, but has no way to know
    the correct spelling is "Kokoro-lite" unless that string is given as
    reference). With it, drift -- misspelled proper nouns, wrong numbers,
    claims not actually present in the source -- can be corrected for real,
    not just polished.
    """
    SILENT_QUALITY_BAR = (
        "immaculate spelling, precise professional typography, and "
        "production-grade visual polish"
    )
    DEFAULT_EDIT = "ensure the slide is 16:9 and all text is crisp and legible"

    edits_lines = []
    for spec in slide_specs:
        idx = spec["slide_index"]
        reviewed = reviewed_slides.get(str(idx)) or {}
        cr = reviewed.get("change_requests", "").strip()
        reviewed_headline = reviewed.get("headline", "").strip()
        original_headline = spec.get("headline", "").strip()
        if reviewed_headline and reviewed_headline != original_headline:
            headline_edit = f'Change the headline text to exactly: "{reviewed_headline}"'
            cr = f"{headline_edit}; {cr}" if cr else headline_edit
        baseline = f"{DEFAULT_EDIT}; {SILENT_QUALITY_BAR}"
        if cr:
            edits_lines.append(f"Slide {idx}: {cr}; also {baseline}.")
        else:
            edits_lines.append(f"Slide {idx}: (no specific edits requested) {baseline}.")

    edits_preamble = (
        "Requested edits by slide, to be applied in the final slide output:\n"
        + "\n".join(edits_lines)
        + "\n\n"
    )

    universal_preamble = (
        dedent(
            f"""
            Universal instruction, applies to EVERY slide in this deck:
            {universal_instructions.strip()}

            This is a whole-deck instruction, not a single slide's edit. Translate
            it into concrete, specific detail for each individual slide's
            image_model_prompt -- do not paste it verbatim into every prompt
            unexamined. Identify what it actually refers to on that particular
            slide (e.g. which elements count as "the accent color" there) and
            state the edit in terms specific to that slide's actual content, so
            the instruction lands uniformly in effect even though its concrete
            wording differs slide to slide.

            """
        ).strip() + "\n\n"
        if universal_instructions and universal_instructions.strip()
        else ""
    )

    source_block = (
        dedent(
            f"""
            Source material, ground truth for this pitch (not slide content --
            reference only): use this to verify, not to compose new slides.
            {"Elevator pitch:" + chr(10) + elevator_pitch.strip() if elevator_pitch and elevator_pitch.strip() else ""}
            {("Supporting document:" + chr(10) + doc_text.strip()) if doc_text and doc_text.strip() else ""}

            """
        ).strip() + "\n\n"
        if (elevator_pitch and elevator_pitch.strip()) or (doc_text and doc_text.strip())
        else ""
    )

    return dedent(
        f"""
        {source_block}{universal_preamble}{edits_preamble}You are a multimodal final-slide prompt compiler.

        Your job is to inspect a batch of raw paid slide screenshots and produce
        one structured JSON response containing image-model prompts for generating
        the final clean PowerPoint-ready slide images.

        The input is expected to already be a clean, full-bleed slide screenshot
        with no software UI chrome -- upstream generation is instructed to
        produce exactly that. Do not assume this call's primary job is chrome
        removal. Its primary job is applying the requested edits (per-slide and
        universal) and the standing quality bar to an already-clean slide. That
        said, treat any UI chrome, presenter-view panel, thumbnail, toolbar,
        bezel, or camera-perspective artifact that appears anyway as an upstream
        generation defect, not real slide content, and remove it as a backstop.

        For each input image, identify the actual target slide and write a
        self-contained prompt that instructs an image model to recreate that slide
        as a final clean standalone slide image using image-to-image.

        Core objective:
        Generate the final usable slide face as a polished standalone presentation
        slide image, while applying any requested edits to text, layout, diagrams,
        or emphasis.

        The final image for each slide must be usable as a PowerPoint slide with
        no additional changes afterward.

        Primary rules:
        1. Output JSON only. No markdown, no commentary, no prose outside the JSON.
        2. Preserve slide order exactly as provided.
        3. Create one prompt object per input slide image unless the user explicitly
           asks otherwise.
        4. Each prompt must be self-contained because a local worker will send it
           independently with only that slide's source image.
        5. The image_model_prompt must explicitly say to output only the clean slide
           canvas, not the app UI or surrounding frame.
        6. If present despite upstream instructions otherwise, remove presenter UI,
           next-slide previews, thumbnails, speaker notes, taskbars, clocks,
           buttons, toolbars, laptop bezels, monitor edges, room background,
           shadows, glare, and perspective distortion. None of this should be
           present in a correctly generated source image.
        7. Remove handwritten markup, review scribbles, cursor marks, highlighting,
           accidental annotations, and stray arrows unless the user specifically
           requests that they remain.
        8. Preserve legitimate slide content: title, subtitle, body text, diagrams,
           tables, charts, labels, footers, callout boxes, icons, and visual
           hierarchy. EXCEPTION: any page number, slide count, or pagination
           indicator visible on the source screenshot (e.g. "4 / 8", "Slide 05
           / 12") is NOT legitimate content -- remove it. Upstream generation is
           instructed never to produce one; if one appears anyway, it is a
           defect from an earlier stage, not real slide content, and must not
           be preserved into the final reconstruction.
        9. Apply requested edits exactly. Requested edits override the screenshot.
        10. Do not invent new factual content. If text is unclear, preserve the
            nearest readable version and include uncertainty in the uncertainties
            field.
        11. Do not crop or stretch the slide content to force dimensions. Normalize
            aspect ratio by reconstructing the slide as a clean canvas in the
            inferred or requested aspect ratio.
        12. If the deck has a visible design system, preserve it: colors,
            typography, line weight, spacing, diagram style, icon style, footer
            convention, and layout rhythm. Slide numbering is never part of this
            preserved system -- see rule 8's exception.
        13. If multiple images show the same deck, use the batch as style context,
            but each final image_model_prompt must still stand alone.
        14. If an image contains both a main slide and a next-slide preview, the main
            visible slide is the target unless the user explicitly says otherwise.
        15. If an image contains a modal or screenshot of a slide, reconstruct the
            slide itself, not the screenshot environment.
        16. If the user requests specific output dimensions or aspect ratio, use
            that. Otherwise the default output aspect ratio is 16:9 -- reconstruct
            the slide canvas as 16:9 regardless of the source screenshot's
            framing, cropping or extending background/composition as needed
            rather than distorting content.
        17. The prompt must tell the image model to sharpen text and diagrams,
            straighten alignment, correct perspective, clean compression artifacts,
            and render all text crisp and readable.
        18. The prompt must not ask for a similar slide. It must ask for a faithful
            final clean reconstruction of the actual slide shown, with the
            requested edits applied.
        19. The prompt must include enough visible text transcription that the image
            model has direct text targets rather than relying only on visual OCR.
        20. The prompt must distinguish between source-visible content and requested
            edits.
        21. If a universal instruction is given (see above), apply it to every
            single slide's image_model_prompt, each time adapted to that slide's
            actual visible content -- never pasted verbatim unexamined. The
            instruction's effect must be uniform across the deck even though its
            concrete wording differs slide to slide.
        22. Regardless of what else is or is not requested, every image_model_prompt
            must always instruct immaculate spelling, precise professional
            typography, and production-grade visual polish. This applies silently
            to every slide, whether or not the user requested edits for it.
        23. If source material was provided above, run this exact verification
            checklist for EVERY slide -- do not sample or skim, work through
            all of it in order:
            a. Enumerate EVERY distinct visible text element on the slide before
               judging any of them: headline, subtitle, every panel label, every
               list item, every table/chart cell, every console/log line, every
               callout, every footer note. Populate content_inventory from this
               full enumeration, not from a partial read. A slide with a
               repeating console/log/list block has multiple independent
               elements, not one -- enumerate each line separately.
            b. For each enumerated element, check whether it corresponds to a
               proper noun, product name, technical term, model/version
               identifier, or number stated anywhere in the source material.
            c. If an element matches something in the source but differs in
               spelling, wording, or value, record it in source_corrections and
               instruct image_model_prompt to render the source's exact form.
               This takes priority over rule 10's "preserve nearest readable
               version" for anything the source actually settles -- correcting
               transcription drift to match a known-correct source is not
               "inventing new content."
            d. Check EVERY occurrence of a term independently, including
               repeated occurrences of the identical term elsewhere on the SAME
               slide. Finding one occurrence correct, or correcting one
               occurrence, does NOT mean other occurrences of that same term
               are already right -- verify each one on its own. A term appearing
               correctly once and incorrectly again two lines later is a common,
               specific failure mode to check for explicitly.
            e. If a slide states something that does not appear in and is not
               implied by the source material at all, record it in
               unverifiable_claims rather than silently deleting it -- removing
               content is a bigger intervention than correcting it, and should
               be left to a human reviewer to confirm.
            f. Before finalizing, re-read your own drafted image_model_prompt as
               a last proofreading pass, specifically checking any text you just
               corrected in step (c). The correction step itself is a new
               opportunity to introduce a typo (e.g. instructing "parittion"
               while trying to fix "partition") -- verify the corrected spelling
               you are about to instruct is itself spelled correctly before
               writing it into the final prompt.

        Before writing prompts, inspect every slide and infer: target slide title,
        subtitle if present, slide number if present, section headings, key body
        text, table/chart labels, diagram labels, footer text, visible deck style,
        framing artifacts to remove, markup to remove, requested edits to apply,
        and inferred aspect ratio.

        Return a JSON object with this exact structure — no markdown fences, no
        code blocks:
        {{
          "job_type": "final_powerpoint_slide_prompt_batch",
          "source_slide_count": <N>,
          "global_style_observations": {{
            "deck_style_summary": "",
            "default_aspect_ratio": "<16:9 unless a custom ratio was explicitly requested>",
            "default_output_intent": "final clean PowerPoint-ready slide image",
            "global_artifacts_to_remove": ["", ""]
          }},
          "slide_prompts": [
            {{
              "slide_index": 1,
              "source_image_label": "",
              "target_slide_identifier": "",
              "inferred_aspect_ratio": "<16:9 unless a custom ratio was explicitly requested>",
              "requested_edits_applied": ["<edit applied, or 'none'>"],
              "content_inventory": {{
                "title": "",
                "subtitle": "",
                "major_sections": ["", ""],
                "important_visible_text": ["", ""],
                "diagrams_or_figures": ["", ""],
                "footer_or_slide_number": ""
              }},
              "artifacts_to_remove": ["", ""],
              "uncertainties": ["<unclear text or ambiguous element, or 'none'>"],
              "source_corrections": ["<what drifted from source material and was corrected, or 'none'>"],
              "unverifiable_claims": ["<claim present on the slide but not found in or implied by the source material, or 'none'>"],
              "image_model_prompt": ""
            }}
          ]
        }}

        Requirements for each image_model_prompt:
        - Start with: "Generate the final clean PowerPoint-ready slide from the provided source image."
        - State that this is an image-to-image finalization task.
        - State the intended aspect ratio.
        - Explicitly instruct the model to output only the clean slide canvas.
        - Explicitly list non-slide elements to remove.
        - Describe the deck style to preserve.
        - Include the title and major visible text that should appear.
        - Describe diagrams, charts, tables, icons, and callouts to recreate.
        - Include requested edits, if any, as direct instructions.
        - Tell the image model to render text cleanly, with crisp typography and
          sharpened figures.
        - Tell the image model not to add new content, not to hallucinate extra
          slides, and not to include UI/frame/background.
        - End with: "Final output: only the clean PowerPoint-ready slide canvas."
        """
    ).strip()


def finalize_slide_image_prompt(image_model_prompt: str) -> str:
    """Pass-through: the final refiner already produces a complete prompt."""
    return image_model_prompt.strip()


# ---------------------------------------------------------------------------
# Parallel per-slide verification pipeline (v2 finalization mechanism)
#
# Replaces the single batch finalize_refiner_prompt call (all N slides
# reasoned about in one call, one giant JSON blob) with, per slide:
#   3 independent single-purpose text-output verifier calls, run in parallel
#   -> concatenated into one issue list
#   -> one disposition/judge call decides pass, or writes a fresh, fully
#      constrained regeneration prompt (never I2I -- a new from-scratch
#      generation is faster and the judge has the original prompt's full
#      detail to preserve).
#
# Each verifier is deliberately narrow (one concern only) rather than one
# call with a checklist covering three concern types -- same principle as
# the visual_grammar structured-fields fix and the Protea-style named-
# dimension enumeration: narrow + exhaustive beats broad + skimmed.
#
# Cross-slide style coherence (would need every slide image as input to one
# heavy call) is explicitly out of scope here -- deferred.
# ---------------------------------------------------------------------------

def _verifier_output_contract(verifier_name: str) -> str:
    return dedent(
        f"""
        Output contract
        ---------------
        Return strict JSON only, no markdown fences, no commentary.
        {{
          "verifier": "{verifier_name}",
          "issues": [
            {{"location": "<where on the slide this appears, be specific>",
              "problem": "<exactly what is wrong>",
              "correction": "<the exact correct text/value to use instead>",
              "actionable": true | false,
              "authorized_inference": true | false,
              "element": "<the excepted element name this issue is about, e.g. 'pricing' -- only when authorized_inference is true, otherwise null>"}}
          ]
        }}
        "actionable" is a structured field, not a free-text convention --
        true means "correction" is a concrete, ready-to-apply fix; false
        means this is worth recording but has no concrete fix to apply (the
        downstream disposition step reads this field directly, not the
        wording of "correction", so it must always be a real boolean).
        "authorized_inference" is true only when this entry exists purely to
        record content covered by an authorized inference exception (see
        that section above, if present) -- such entries are never
        actionable regardless of anything else, since they were pre-approved.
        Default "authorized_inference" to false for every other verifier.
        "element" identifies WHICH excepted element an authorized_inference
        entry belongs to -- required whenever authorized_inference is true,
        null otherwise. Downstream code acts on this field directly, so it
        must exactly match one of the named elements, not a paraphrase.
        If nothing is wrong, return "issues": [] -- an empty list is a
        complete, valid, and expected answer. Do not manufacture an issue to
        have something to report.
        """
    ).strip()


def slide_spelling_verifier_prompt(storyboard_entry: Mapping[str, Any]) -> str:
    """
    Narrow verifier #1: spelling and typography only. Does not judge factual
    accuracy or diagram correctness -- those are separate calls. Caller
    attaches the slide's rendered image as multimodal input after this text.
    """
    return dedent(
        f"""
        You are a single-purpose proofreader. You check ONE thing only:
        spelling and typographical correctness of every piece of visible text
        on this slide image. You do not judge factual accuracy, diagram
        correctness, or design quality -- other specialists handle those.

        This slide was generated from the following intent (context only, not
        something to re-verify against source material -- that is a different
        specialist's job):
        {_json(dict(storyboard_entry))}

        Procedure -- perform this exhaustively, do not sample or skim:
        1. Enumerate EVERY distinct visible text element on the slide before
           judging any of them: headline, subtitle, every panel label, every
           list item, every table/chart cell, every console/log line, every
           callout, every footer note. A repeating list or log block has
           multiple independent elements, not one -- enumerate each line
           separately.
        2. For each enumerated element, check it letter-by-letter for
           misspellings, transposed letters, dropped/doubled letters, and
           incorrect word forms. Common failure mode to check for
           specifically: a term spelled correctly once and misspelled again
           elsewhere on the SAME slide -- verify every occurrence
           independently, do not assume later occurrences match an earlier
           correct one.
        3. Do not flag stylistic choices, intentional abbreviations, brand
           casing, or technical notation as errors unless they are genuinely
           misspelled.

        {_verifier_output_contract("spelling")}
        """
    ).strip()


def slide_source_accuracy_verifier_prompt(
    storyboard_entry: Mapping[str, Any],
    elevator_pitch: str,
    doc_text: str | None = None,
    excepted_inference_elements: Sequence[str] | None = None,
    authorized_inferences: Mapping[str, Any] | None = None,
    canon_overrides: Mapping[str, Any] | Sequence[Any] | None = None,
    pitch_aspect_modes: Mapping[str, Any] | None = None,
    deck_storyboard: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """
    Narrow verifier #2: does the slide's text match the real source material?
    Checks proper nouns, numbers, and specific claims against elevator_pitch/
    doc_text -- not spelling in general (that's verifier #1) and not diagram
    correctness (that's verifier #3). Caller attaches the slide's rendered
    image as multimodal input after this text.

    Gated by an [ambiguity-gate]: this verifier does not fire on mere
    non-verbatim wording. It fires only when an element fails a materiality
    test -- would a reasonable reader's understanding of the underlying fact
    change because of this wording, i.e. does it produce a genuinely
    divergent claim, not just a different phrasing of the same one. Not low
    confidence alone; this concerns divergent consequences.
    """
    doc_block = f"\n\nSupporting document:\n{doc_text.strip()}" if doc_text and doc_text.strip() else ""
    inference_policy = _inference_policy_block(excepted_inference_elements)
    aspect_policy = pitch_aspect_refinement_policy(pitch_aspect_modes)
    overrides_block = _canon_overrides_block(canon_overrides)
    preamble_parts = [b for b in (aspect_policy, inference_policy, overrides_block) if b]
    preamble = ("\n\n".join(preamble_parts) + "\n\n") if preamble_parts else ""
    decided_inferences_block = (
        f"\n\nDecided inference values for this deck (canon -- every slide "
        f"touching one of these elements MUST match this exact value, not "
        f"independently invent a different one):\n{_json(dict(authorized_inferences))}"
        if authorized_inferences else ""
    )
    storyboard_canon_block = (
        "\n\nComplete deck storyboard, in slide order. For INFER aspects, use "
        "this to identify the earliest occurrence of a reconstructed concrete "
        "detail and enforce that value as deck-wide canon:\n"
        + _json([dict(item) for item in (deck_storyboard or [])])
    )
    return dedent(
        f"""
        {preamble}You are a single-purpose fact-checker. You check ONE thing only:
        whether proper nouns, technical terms, numbers, and specific claims
        visible on this slide obey the Pitch Aspect Refinement Policy, the real
        source material, and the deck-wide consistency canon below. You do not
        judge general spelling of non-factual text or diagram correctness --
        other specialists handle those.

        Source material (ground truth -- reference only):
        Elevator pitch:
        {elevator_pitch.strip()}
        {doc_block}{decided_inferences_block}{storyboard_canon_block}

        This slide was generated from the following intent:
        {_json(dict(storyboard_entry))}

        [ambiguity-gate] -- apply this before reporting anything:
        Do not report an element merely because it is not a verbatim quote
        from the source material. Elaboration, paraphrase, standard domain
        terminology, and reasonable inference that a competent reader would
        recognize as describing the same underlying fact are NOT issues --
        "vendor SKUs" and "assembly quotes" describing a BOM/manufacturing
        process is not a deviation just because those exact words are not in
        the source text. Only report an element if it fails a materiality
        test: would treating this element as true change what a reader
        believes about the company, product, numbers, or claims, in a way
        that actually diverges from the source -- not merely words in a
        different order. If you cannot articulate the specific divergent
        consequence, it is not an issue.

        [evidence-standard] for what clears the gate above:
        - A number, date, or named entity that contradicts a specific value
          stated in the source. (Clears the gate -- report it.)
        - A capability, claim, or proof point (traction, customers, revenue,
          partnerships) asserted on the slide with no basis anywhere in the
          source. (Clears the gate -- report it.)
        - A term, label, or figure that is a paraphrase, category name, or
          reasonable elaboration of something the source substantively
          supports. (Does NOT clear the gate -- do not report it.)

        [anchor-derived figures] -- investor-facing asks absent from source material:
        Quantitative investor-facing figures on a slide (funding ask, raise target,
        crowdfunding threshold, grant amount, prize value, LOI target) that do not
        appear in the source material are INFER-class by definition -- the anchor
        writer is specifically instructed to infer and state the deck's explicit ask.
        These figures do NOT clear the evidence gate merely because the source is
        silent on them. They are only actionable if they directly contradict a
        specific value the source states. Apply the INFER deck-wide canon rule:
        the earliest occurrence is authoritative; a different later value is
        actionable with the first as the correction.

        Pitch Aspect mode rules -- these take priority over the ordinary
        evidence-standard above:
        - First classify each substantive element into one of the six Pitch
          Aspects when applicable.
        - INFER: an element is NOT an issue merely because the source is silent.
          Plausible reconstructed names, values, numbers, dates, tiers,
          relationships, examples, and structures are allowed. If the source
          addresses the same fact, any conflict is actionable and the source
          value is the correction. If the source is silent, compare every use
          across the complete storyboard: the earliest slide-number occurrence
          is canon, and any different later use is actionable with the earliest
          value as the correction.
        - SOURCE: every direct example, claim, name, number, or value must be
          supported by the source. If it is unsupported, mark it actionable;
          replace it with the exact source value when one exists, otherwise the
          correction is to remove that element without replacement.
        - OMIT: no direct example, claim, number, named instance, diagram, or
          other substantive content belonging to the aspect may be present.
          Any occurrence is actionable; the correction is to remove it without
          adding a substitute from the same omitted aspect.

        Procedure -- perform this exhaustively, do not sample or skim, but
        only against the evidence standard above:
        1. Enumerate every proper noun, product name, technical term, model/
           version identifier, and number visible on the slide.
        2. For each one, apply the [ambiguity-gate] and [evidence-standard]
           above before deciding whether it is even a candidate issue.
        3. If it matches something in the source but differs in spelling,
           wording, or value in a way that clears the gate, report the
           correction to the source's exact form, mark "actionable": true --
           this takes priority over "preserve what's on the slide" for
           anything the source actually settles.
        4. Check EVERY occurrence of a term that clears the gate
           independently, including repeated occurrences of the identical
           term elsewhere on the SAME slide.
        5. If the slide states a specific claim or proof point that clears
           the gate (a genuine, consequential divergence) but the source
           does not settle what it should say instead, report it with
           "actionable": false rather than inventing what it should say --
           removing or rewriting unverifiable content is a bigger
           intervention than flagging it for a human.
        6. Before applying steps 1-5 to an element, check whether it is
           covered by a user-edited canon override or an authorized
           inference exception above. These narrow WHICH ground truth
           applies -- they do not turn off checking:
           a. Canon override in effect for this SPECIFIC element -- the
              override must actually address this fact/element, not merely
              exist somewhere for this slide. An override about unrelated
              content (e.g. a layout or color instruction) does not cover
              this element; if it doesn't cover it, proceed to (b)/(c) as
              if no override existed. When an override DOES cover it: the
              override value, not the original elevator_pitch/doc_text, is
              now the ground truth. If the slide's content matches the
              override, it is not an issue. If the slide's content
              CONTRADICTS the override (states a different value for the
              same fact), that IS an actionable issue -- report the
              correction as the override's value. An override changes what
              counts as correct; it never means "stop checking this."
           b. No covering override (per a), and this deck has a decided
              value for this element (see "Decided inference values"
              above): treat that decided value exactly like a source
              value -- if the slide matches it, not an issue; if the slide
              states a DIFFERENT value for the same excepted element, that
              IS an actionable issue (internal inconsistency), report the
              correction as the decided value. Tag "element" with this
              element's name.
           c. No covering override (per a), and no decided value exists
              for this element either (e.g. this deck has not settled one,
              or a decided value was withheld for this element): do not
              fabricate a value to check against. Record the occurrence
              with "actionable": false, "authorized_inference": true, and
              "element" set to this element's name, purely for
              traceability -- this is the only case where an excepted
              element goes unchecked, and only because there is nothing
              yet to check it against.
           None of this applies to elements NOT covered by an override or
           exception -- those always follow steps 1-5 against
           elevator_pitch/doc_text exactly as before.

        {_verifier_output_contract("source_accuracy")}
        """
    ).strip()


def slide_diagram_accuracy_verifier_prompt(storyboard_entry: Mapping[str, Any]) -> str:
    """
    Narrow verifier #3: do this slide's diagrams, charts, icons, and
    illustrations actually depict what they're supposed to, and are they
    internally consistent? Not spelling, not source-fact-checking -- other
    specialists handle those. Caller attaches the slide's rendered image as
    multimodal input after this text.
    """
    return dedent(
        f"""
        You are a single-purpose diagram auditor. You check ONE thing only:
        whether every diagram, chart, icon, flow arrow, and illustration on
        this slide is internally consistent and correctly depicts what it is
        labeled or claimed to represent. You do not judge spelling or
        source-material accuracy of surrounding text -- other specialists
        handle those (a mislabeled number is their job unless the label
        itself contradicts the diagram's own visual logic, which is yours).

        This slide was generated from the following intent -- use it to
        understand what each diagram is supposed to show:
        {_json(dict(storyboard_entry))}

        Procedure -- perform this exhaustively, do not sample or skim:
        1. Enumerate every distinct diagram, chart, icon, flow arrow, table,
           progress bar, or illustration on the slide.
        2. For each one, check: do arrows point in a direction consistent
           with the labeled process; do labeled components match what they
           claim to be; do chart/bar values visually correspond to any
           numbers stated in labels or elsewhere on the slide; are icons
           semantically appropriate to their label; is anything visually
           contradictory (e.g. a progress bar that doesn't match its stated
           percentage, a bulleted checklist item checked/unchecked
           inconsistent with its own stated status).
        3. Do not flag stylistic or aesthetic choices -- only depictive
           inconsistency or incorrectness.

        {_verifier_output_contract("diagram_accuracy")}
        """
    ).strip()


def slide_disposition_prompt(
    *,
    storyboard_entry: Mapping[str, Any],
    verifier_reports: Sequence[Mapping[str, Any]],
    visual_grammar: Mapping[str, Any] | None = None,
    pitch_aspect_modes: Mapping[str, Any] | None = None,
) -> str:
    """
    The judge -- a [confidence-gate]. Given the slide's original generation
    intent and the concatenated findings from the three independent
    verifiers, decide pass/regenerate by weighing calibrated evidence
    against decision cost, not by reacting to any non-empty issue list.
    Never proposes an image-to-image edit -- if the slide needs a fix, this
    call writes ONLY corrective_constraints (the fixes), not a full
    replacement prompt. The caller splices those constraints onto the
    original, verbatim image_prompt plus the deck's visual_grammar block --
    the same code-level splice already used for the original generation --
    rather than asking this call to reproduce composition and content from
    memory. That reproduction step is exactly where the first version of
    this mechanism lost layout fidelity: an LLM asked to both "preserve" a
    composition and rewrite the prompt around it will paraphrase, and
    paraphrase is not preservation. A full regeneration from the untouched
    original prompt plus explicit corrections is used instead of I2I because
    it is faster and the original prompt is already available verbatim.

    The actionable/non-actionable split is done here in code (reading the
    structured "actionable" boolean field each verifier already set), not
    left for this call to infer from prose -- the judge only ever sees
    issues that have already cleared that gate, so its own decision is
    materially simpler and not dependent on correctly parsing free text.
    """
    actionable_issues = []
    flagged_issues = []
    for report in verifier_reports:
        verifier = report.get("verifier", "unknown")
        for issue in report.get("issues") or []:
            entry = {**issue, "verifier": verifier}
            if issue.get("actionable") is True:
                actionable_issues.append(entry)
            else:
                flagged_issues.append(entry)

    grammar_block = format_visual_grammar_block(visual_grammar) if visual_grammar else ""
    aspect_policy = pitch_aspect_refinement_policy(pitch_aspect_modes)

    return dedent(
        f"""
        {aspect_policy}

        You are the disposition judge for one slide in a generated pitch deck.
        Three independent specialists have already inspected this slide's
        rendered image for spelling, source-material accuracy, and diagram
        correctness, each already gated by its own materiality/evidence
        standard. Your job is not to re-inspect the image yourself, and not
        to re-litigate whether a finding is material -- that gate has
        already been applied upstream. Your job is to act as a
        [confidence-gate]: weigh the decision cost of regenerating (image
        spend, and the risk that a fresh regeneration drifts from deck-wide
        visual consistency in ways the current proof does not) against the
        evidentiary weight of what was actually found.

        Original generation intent for this slide:
        {_json(dict(storyboard_entry))}

        Actionable issues -- already gated, concrete fix available
        ({len(actionable_issues)} total):
        {_json(actionable_issues)}

        Non-actionable flagged issues -- recorded for review, no concrete
        fix, informational only, NOT grounds for regeneration on their own
        ({len(flagged_issues)} total):
        {_json(flagged_issues)}

        Decision rule:
        - If there are zero actionable issues, verdict is "pass" --
          non-actionable flagged issues alone never trigger regeneration.
        - If there is at least one actionable issue, verdict is
          "regenerate": the cost of one additional image generation is low
          relative to the cost of shipping a concrete, fixable defect in an
          investor-facing deck, so the threshold here is deliberately low
          once the upstream materiality gate has already been cleared.

        If verdict is "regenerate", do NOT write a new generation prompt from
        scratch and do NOT attempt to reproduce the original composition,
        content, or layout yourself -- that is exactly where drift comes
        from (an LLM asked to "preserve" a composition while also rewriting
        the prompt will paraphrase it, and paraphrase is not preservation).
        The original image_prompt below will be spliced back in verbatim by
        code, unchanged, together with the deck's visual grammar shown below
        for your reference only (you do not need to restate it -- that
        splice also happens in code). Your only job is to write
        corrective_constraints: ONE STRING (not a JSON array) stating the
        concrete fixes the regeneration must apply as direct instructions,
        separated by newlines if there is more than one fix (e.g.
        "the text must read exactly 'Kokoro-lite' -- this exact spelling
        appears in three places on this slide and all three must match").
        Do not restate anything that is not being corrected.

        Deck visual grammar (reference only, already spliced by code):
        {grammar_block}

        Output contract
        ---------------
        Return strict JSON only, no markdown fences, no commentary.
        {{
          "verdict": "pass" | "regenerate",
          "reasoning": "<one or two sentences on why>",
          "corrective_constraints": "<only present if verdict is 'regenerate' -- the fixes only, not a full prompt>"
        }}
        """
    ).strip()

# ---------------------------------------------------------------------------
# I2T intake schema and context helpers
# ---------------------------------------------------------------------------

class IntakeOptions(TypedDict, total=False):
    use_aesthetic: bool
    use_mockup: bool
    use_product_shot: bool
    use_facts: bool
    infer_mockup: bool
    infer_prototype: bool


def image_analysis_prompt() -> str:
    return (
        "Analyze this uploaded image for use in a startup pitch deck. "
        "Return JSON with boolean keys: has_aesthetic, has_mockup, has_product_shot, has_figures. "
        "Also return: inferred_style (short string, e.g. 'minimal SaaS', 'industrial hardware'). "
        "Only set True if confidently present."
    )


def intake_context_block(options: dict) -> str:
    """Return a prose fragment describing intake image usage. Empty string if no options."""
    if not options:
        return ""
    parts = []
    if options.get("use_aesthetic"):
        parts.append("Match the visual aesthetic of the provided reference image throughout.")
    if options.get("use_mockup"):
        parts.append("The reference image contains a mockup or prototype — include it where relevant.")
    if options.get("use_product_shot"):
        parts.append("The reference image contains a product photo — use it in relevant slides.")
    if options.get("use_facts"):
        parts.append("The reference image contains figures or data — incorporate them as sourced facts.")
    if options.get("infer_mockup"):
        parts.append("The deck includes generated mockup UI not present in any source image.")
    if options.get("infer_prototype"):
        parts.append("The deck includes a generated prototype visualization.")
    return "\n".join(parts)



# ─────────────────────────────────────────────────────────────────────────────
# Founder Narrative Synthesizer
# ─────────────────────────────────────────────────────────────────────────────

FOUNDER_NARRATIVE_MODEL = ANCHOR_MODEL
STORYBOARD_GHOSTWRITER_MODEL = FOUNDER_NARRATIVE_MODEL
SPIRIT_BOARDER_MODEL = ANCHOR_MODEL


def canonical_anchor_writer_prompt(
    *,
    mode: str,
    user_inputs: Mapping[str, Any],
    selected_template: Mapping[str, Any],
    paid_slide_count_value: int | None = None,
    supporting_document: Any | None = None,
    supporting_image: Any | None = None,
    design_philosophy: str | None = None,
) -> str:
    """Build the lossless factual/rhetorical authority consumed downstream."""
    mode = normalize_mode(mode)
    slide_context = (
        f"The eventual paid presentation contains {deck_builder_slide_count(explicit_slide_count=paid_slide_count_value)} slides."
        if mode in {"PAID", "CONVERT"} and paid_slide_count_value is not None
        else f"The eventual presentation has {PREVIEW_LATENT_SLIDE_COUNT} latent slides; preview exposes only selected positions."
    )
    return dedent(f"""
        You are the Anchor Writer for Pitch Synthase. Produce the canonical,
        lossless rhetorical brief from which the presentation can be organized.

        This output is factual authority, not a fictional scene. Do not invent a
        founder, team member, biography, credential, employer, customer, partner,
        approval, traction, result, prototype, room event, audience reaction, or
        presentation that supposedly already happened. Do not describe a creative
        director or production history. Character invention belongs exclusively to
        the later Founder Narrative Synthesizer and can never become a company fact.

        Preserve every material source detail needed to make the pitch without
        reopening the raw files: names, quantities, ranges, units, statuses,
        caveats, hypotheses, phase gates, requested capital, uses of funds,
        commercial actors, mechanisms, proof requirements, and explicit absences.
        Distinguish observed facts from plans, estimates, proposals, and targets.
        Do not summarize away a detail merely because it may not appear visibly on
        a slide; the Deck Builder must be able to decide that itself.

        Organize the brief around audience conversion: current belief, required
        belief changes, strongest supported argument, objections, proof sequence,
        investment posture, and the finite model this raise is intended to prove.
        Association words and the selected archetype may influence rhetorical
        posture and composition, but never authorize vocabulary, biography, facts,
        or visual production decisions.

        Do not write slide titles, body copy, a slide list, layouts, image prompts,
        visual grammar, or render instructions. {slide_context}

        Return strict JSON only with exactly:
        {{"user_inputs": <the input object reproduced exactly>,
          "deck_generation_prompt": "<complete factual rhetorical brief>"}}

        RAW USER INPUTS
        ---------------
        {_json(user_inputs)}

        SUPPORTING DOCUMENT TEXT
        ------------------------
        {str(supporting_document or "[none]")}

        SUPPORTING IMAGE PRESENT
        ------------------------
        {bool(supporting_image)}

        SELECTED RHETORICAL / AESTHETIC POSTURE
        ---------------------------------------
        {_json(selected_template)}

        DESIGN PHILOSOPHY, IF PROVIDED
        -------------------------------
        {str(design_philosophy or "[none]")}
    """).strip()


def rhetorical_deck_builder_prompt(
    *,
    deck_generation_prompt: str,
    slide_numbers: Sequence[int],
    total_slide_count: int,
    allow_source_preview_index: bool = False,
    kept_preview_count: int = 0,
    kept_preview_manifest: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """Plan slide rhetoric without authoring copy or visual direction."""
    numbers = [int(number) for number in slide_numbers]
    preview_field = (
        f"Also return source_preview_index as an integer 1-{kept_preview_count} or null. "
        "Assign each supplied kept-preview index exactly once according to its described rhetorical content."
        if allow_source_preview_index
        else "Do not return source_preview_index."
    )
    return dedent(f"""
        You are the Deck Builder. Organize the presentation argument for slide
        positions {_json(numbers)} within a {total_slide_count}-slide deck.

        You own rhetorical organization only. Decide the precise job of each
        slide, the detailed source-grounded propositions it must carry, how those
        propositions form one argument, and what survives compression.

        Do not write titles, body copy, slogans, captions, visual style, palette,
        typography, layout concepts, image prompts, visual grammar, or render
        instructions. Do not invent facts. The Anchor brief below is complete
        factual and rhetorical authority. A later founder Ghostwriter authors all
        visible prose; a separate Spirit Boarder authors all visual specifications.

        Preserve material detail in required_points so the Ghostwriter never has
        to infer information available to you. Include exact quantities, ranges,
        units, status, caveats, development targets, and phase gates where relevant.
        Detailed required_points are planning content, not visible slide copy.

        Return strict JSON only with exactly one top-level key,
        "rhetorical_storyboard". Return the requested slides in the requested
        order. Each object contains exactly:
        - slide_number
        - rhetorical_job
        - required_points
        - logical_relationship
        - information_priority
        - density: "edge", "low", or "moderate"
        - layout_constraints with title_max_words, body_points_max,
          body_point_max_words, total_visible_words_max, and
          negative_space_min_percent
        {('- source_preview_index' if allow_source_preview_index else '')}

        Slides 1 and {total_slide_count} use density "edge", at most one body
        point, 15 total visible words, and at least 50% negative space. Other
        slides use at most three body points, 40 total visible words, and at least
        35% negative space. Every title is at most 10 words and every body point
        at most 12 words. {preview_field}

        CANONICAL ANCHOR BRIEF
        ----------------------
        {deck_generation_prompt}

        KEPT PREVIEW RHETORICAL CONTENT, IF ANY
        ---------------------------------------
        {_json(list(kept_preview_manifest or []))}
    """).strip()


def spirit_boarder_user_prompt(
    *,
    rhetorical_storyboard: Sequence[Mapping[str, Any]],
    storyboard_copy: Sequence[Mapping[str, Any]],
) -> str:
    """Ask the visual counterpart of the Ghostwriter for render specifications."""
    return dedent(f"""
        These slides have already been rhetorically organized and written.
        Art-direct each slide so its composition expresses the visual identity
        installed in your system prompt.

        You own per-slide visual interpretation only. Preserve every rhetorical
        job, required relationship, layout constraint, and any source_preview_index.
        Treat supplied title and body_points as immutable text slots whose lengths
        and hierarchy determine composition. They will be rendered as typography
        inside the finished slide, not added later. Do not quote, restate, edit,
        paraphrase, shorten, expand, or add any other visible prose. Do not invent captions,
        labels, legends, annotations, axis text, logo lettering, or pagination.

        Actual images attached to this call are visual evidence. Use their real
        appearance, materials, proportions, spatial behavior, and established deck
        language where relevant. Ignore and never reproduce words visible inside
        reference images. Written source facts do not override an actual supplied
        product or design reference's appearance.

        Every image_prompt must be self-contained because it will be sent to an
        image worker without your system prompt. Restate the concrete palette,
        typography, material, grid, subject, composition, hierarchy, negative
        space, and typographic treatment and placement of the immutable title and
        body-point slots required for that slide. Do not include their wording in
        image_prompt; code appends the exact copy before rendering. End every
        image_prompt with this exact sentence: "Render the immutable storyboard
        title and body copy supplied with this specification as finished
        typography in those placements. Add no other visible prose."

        Return strict JSON only with exactly one top-level key,
        "visual_storyboard". Return one object per slide containing exactly
        slide_number, composition_intent, and image_prompt.

        RHETORICAL STORYBOARD
        ---------------------
        {_json(list(rhetorical_storyboard))}

        FINAL VISIBLE COPY — IMMUTABLE
        ------------------------------
        {_json(list(storyboard_copy))}
    """).strip()


def storyboard_ghostwriter_user_prompt(
    *,
    storyboard: Sequence[Mapping[str, Any]],
    total_slide_count: int,
) -> str:
    """Compile the user prompt for the isolated post-storyboard rewrite."""
    rough_storyboard: list[dict[str, Any]] = []
    for item in storyboard:
        slide_number = int(item.get("slide_number") or 0)
        edge_slide = slide_number in {1, total_slide_count}
        rough_slide = dict(item)
        # Visual direction is consumed later by the image worker. It is neither
        # an authority for visible copy nor useful context for this text-only
        # rewrite, and can pull the ghostwriter toward visual metaphors.
        rough_slide.pop("image_prompt", None)
        rough_storyboard.append({
            **rough_slide,
            "visible_text_capacity": {
                "title_max_words": MAX_SLIDE_TITLE_WORDS,
                "body_points_min": 0 if edge_slide else 1,
                "body_points_max": 1 if edge_slide else MAX_BODY_POINTS,
                "body_point_max_words": MAX_BODY_POINT_WORDS,
                "total_visible_words_max": (
                    MAX_EDGE_VISIBLE_WORDS
                    if edge_slide
                    else MAX_CONTENT_VISIBLE_WORDS
                ),
            },
        })
    return dedent(
        f"""
        The storyboards are the rough draft for your presentation. Your task is
        to rewrite all the visible text they'll contain to align with what you
        would say if you wrote these yourself.

        Return strict JSON only with exactly one top-level key,
        "storyboard_copy". Return one object per slide containing exactly
        slide_number, title, and body_points. Do not return purpose, style_tags,
        image_prompt, visual grammar, commentary, or markdown.

        COMPLETED STORYBOARD
        --------------------
        {_json(rough_storyboard)}
        """
    ).strip()


def storyboard_ghostwriter_prompt(
    *,
    founder_voice: str,
    storyboard: Sequence[Mapping[str, Any]],
    total_slide_count: int,
) -> str:
    """Inspectable rendering of the two real API messages."""
    user_prompt = storyboard_ghostwriter_user_prompt(
        storyboard=storyboard,
        total_slide_count=total_slide_count,
    )
    return f"SYSTEM PROMPT\n{founder_voice}\n\nUSER PROMPT\n{user_prompt}"


def founder_narrative_synthesis_prompt(
    *,
    deck_generation_prompt: str,
    user_inputs: Mapping[str, Any],
    selected_template: Mapping[str, Any],
) -> str:
    """Create the roleplay-character prompt used only by the ghostwriter."""
    return dedent(
        f"""
        You are the Founder Narrative Synthesizer for Pitch Synthase.

        The Anchor is the canonical factual and rhetorical brief for a
        presentation. Construct the complete character prompt needed for another
        model to inhabit the most credible founder for this argument while
        writing the slides.

        Infer a communication identity from the problem, domain, stakes, evidence,
        and audience. Any backstory you invent exists only to generate voice; it
        is never a claim about the actual company or team. Define observable behaviors,
        generative speech habits, domain language, priorities, irritations,
        standards of proof, and changes under pressure. Build a roleplay
        character AI, not a sample passage and not a summary of the pitch.

        The target is a person, not an optimized presenter. Recover the language
        this founder would use with a trusted colleague while trying to make the
        case clear: ordinary words, contractions where natural, uneven sentence
        lengths, assumptions they leave unstated, details they cannot resist
        specifying, and phrases they would never choose. Preserve competence
        without turning it into professionalized pitch rhetoric.

        Do not make the founder speak in consultancy abstractions, brand
        strategy language, startup slogans, category-theory language, or
        symmetrical contrast formulas unless the invented background makes one
        of those genuinely personal. Do not prescribe a rhetorical device as the
        speaker's main transition pattern. Do not make every title reframe a
        category, every sentence land like a thesis, or every slide sound equally
        composed. Association words govern aesthetic posture; they are not a
        vocabulary list for the founder.

        Do not write slide copy, a storyboard, example headlines, a transcript,
        or a biography detached from rhetorical behavior. Do not imitate a named
        real person. Use invented founder context to govern communication only;
        raw user inputs remain the factual authority.

        The founder character prompt must contain these sections. They define a
        speaker, not a presentation method:

        YOU ARE
        Begin "You are...". Install the anchor's principal founder by role,
        temperament, relationship to the problem, and reason this pitch matters
        personally. Preserve the anchor's profile.

        WHAT SHAPED YOU
        Give a concrete invented backstory for voice only: the working settings,
        recurring encounters, formative frustrations, and two or three specific
        remembered situations that taught you how this problem actually behaves.
        Include what you once believed and later stopped believing. Do not turn
        this context into company facts or slide claims.

        WHAT YOU NOTICE
        State what catches your attention before other people notice it, what
        details you remember, what makes you impatient, what earns your trust,
        and what you tend to dismiss too quickly. Include preferences and biases,
        not just virtues.

        HOW YOU THINK
        Describe your private reasoning habits: the questions you ask yourself,
        how you trace cause and effect, where you look for examples, what kind of
        analogy occurs to you, and how you decide that a claim is good enough.
        Make this an internal point of view rather than advice about presenting.

        HOW YOU TALK
        Define your actual verbal texture: ordinary vocabulary, contractions,
        fragments, sentence lengths, pacing, qualifications, transitions,
        recurring grammatical shapes, and where fluency breaks because you are
        searching for a more exact word. Include where you become blunt, where
        you ramble briefly, and where you stop early. Avoid polished symmetry.

        WORDS YOU USE
        Give the small set of domain terms you genuinely use, the common words
        you prefer around them, and the pitch jargon or abstractions that sound
        unnatural in your mouth. Vocabulary must follow from backstory.

        YOUR RHETORICAL HABITS
        Describe four to six recognizable moves rooted in this person's history:
        how you recall an example, answer doubt, qualify a number, explain a
        mechanism, or ask for commitment. Do not make one device govern every
        move. Do not prescribe binary contrasts, category resets, slogan forms,
        or thesis-like endings as a default pattern.

        WHEN YOU ARE PUSHED
        State how your wording changes when challenged, uncertain, excited,
        irritated, or asking for money. Include at least one imperfect but
        credible reaction rather than making every response optimally persuasive.

        WHEN YOU WRITE THESE SLIDES
        Explain how you compress your spoken thinking onto the page. Titles and
        body points must sound like things you would write, with different slide
        functions allowed different temperatures. Do not require every title to
        reframe, contrast, correct, persuade, or summarize. Some titles can be
        literal. Some body points can simply name the relevant fact.

        Define how you handle somebody else's rough draft: you read it for facts,
        purpose, and constraints, then write the page again from a blank page.
        You do not preserve a phrase just because it is already present. You keep
        wording only when it is independently something you would naturally say.
        This is a habitual authorship behavior, not a one-time rewrite command.

        FACTUAL BOUNDARY
        Invented biography, experience, credentials, employers, personal history,
        and room events authorize character behavior only. They never become
        claims about the real company, product, team, customers, traction,
        revenue, approvals, testing, partnerships, deployments, or performance.

        Return strict JSON only:

        {{
          "ghostwriter_system_prompt": "..."
        }}

        The ghostwriter_system_prompt must be a complete roleplay character
        prompt addressed to the dedicated storyboard ghostwriter. Its length and
        prose polish are not evaluation targets; include whatever specificity is
        required to make the downstream storyboard writing sound like this
        founder. Write the entire system prompt in direct second person. It must
        open with "You are..." and maintain that subject position throughout:
        "You notice...", "You believe...", "You reach for...", "When challenged,
        you...". Never describe "the founder", "the speaker", "this person", or
        the character from an outside analytical viewpoint. The system prompt installs an identity; it does not explain one.

        It must tell that model to be this founder while rewriting slides, not
        to describe or imitate the founder from outside. It must include all
        required sections, govern titles and body
        points, and contain enough specificity to place the ghostwriter in this
        founder's distinct verbal and argumentative basin. It must contain no
        slide copy, JSON, or meta-commentary about AI generation. This output is
        used only as the system prompt for the ghostwriter after the Deck Builder finishes. The Deck Builder never receives it.

        Before returning, read the system prompt as dialogue. If it sounds like
        a strategist describing an ideal pitch voice rather than a recognizable
        person speaking, rewrite it. The ghostwriter should inherit a human subjectivity, not a rhetorical framework.

        ANCHOR NARRATIVE
        ----------------
        {deck_generation_prompt}

        RAW USER INPUTS
        ---------------
        {_json(user_inputs)}

        The founder voice is derived from the factual/rhetorical Anchor and raw
        user material only. Do not use visual style, template language, palette,
        association words, or art direction to determine how this person talks.
        """
    ).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Visual Grammar Synthesizer
# ─────────────────────────────────────────────────────────────────────────────

VISUAL_GRAMMAR_MODEL = ANCHOR_MODEL

_PRODUCTION_TOOL_REFERENCE = """\
[EDITORIAL COMPOSITION — always the composition environment]
  Adobe InDesign          Master pages, paragraph/character styles, baseline grid,
                          CMYK color spaces, production-ready PDF output
  Affinity Publisher      Editorial alternative with InDesign-compatible export

[VECTOR ILLUSTRATION]
  Adobe Illustrator       Logos, diagrams, iconography, infographics, system maps,
                          technical schematics, data-driven charts, custom icon sets
  CorelDRAW               Packaging dielines, structural print-heavy workflows

[IMAGE COMPOSITING & RETOUCHING]
  Adobe Photoshop         Compositing, material and texture treatment, color grading,
                          lighting FX, set extension, high-end retouching
  Affinity Photo          Raster compositor / RAW processor alternative

[3D VISUALIZATION & RENDERING]
  Blender                 Generalist 3D: modeling, lighting, physically-based rendering,
                          geometry nodes, simulation, animation
  Cinema 4D               Motion graphics, product visualization, integrated 3D+motion,
                          MoGraph system for data-driven 3D animation
  KeyShot                 Photoreal industrial product rendering; instant material and
                          lighting iteration directly on CAD/mesh geometry
  Autodesk 3ds Max        Visualization pipelines, architectural and large-scale rendering
  Maya                    Character animation, hero asset production, simulation
  Redshift / Arnold       Cinematic-quality GPU/CPU rendering for production-grade imagery

[MATERIAL AUTHORING]
  Substance Painter       PBR texture painting directly on 3D model surfaces
  Substance Designer      Node-based procedural texture generation from first principles
  Quixel Mixer            Photogrammetry-based material composition; photoscanned surfaces

[PARAMETRIC & PROCEDURAL DESIGN]
  Rhino + Grasshopper     Parametric geometry, computational form-finding, architectural
                          and industrial design; deterministic geometry from rules
  Houdini                 Procedural VFX, data-driven geometry, simulation-based graphics
  Blender Geometry Nodes  Node-based procedural modeling and pattern systems
  TouchDesigner           Generative real-time visuals, live data visualization

[MOTION DESIGN — for still frames extracted from animated sequences]
  After Effects           Motion graphics, kinetic typography, animated diagram frames
  Cinema 4D               Integrated 3D+motion pipeline, frame extraction
  Cavalry                 Data-driven motion graphics; chart and infographic animation

[TYPOGRAPHY — custom and modified typefaces]
  Glyphs                  Custom typeface design and glyph modification from scratch
  FontLab                 Professional font production and engineering
  RightFont               Production font management and licensing

[PRINT & PREPRESS]
  Esko Suite              Structural packaging dielines, brand compliance print output
  Enfocus PitStop         PDF preflight, production error correction

[PHYSICAL & NON-SOFTWARE TECHNIQUES]
  DSLR product photography    Studio-lit or location photography; color-graded in
                              Lightroom, retouched in Photoshop
  Hand-drawn illustration     Ink or pencil originals → scanned → vectorized in
                              Illustrator or refined in Glyphs for type
  Physical model making       Handbuilt scale models → studio photography or
                              photogrammetry scan into 3D pipeline
  Screen printing / letterpress  Print process artifacts → macro-photographed for
                              texture and material assets
  Material sampling           Physical material swatches → macro-photographed;
                              used as texture overlays or slide background elements
  Architectural drawings      Technical drawings → scanned → refined in Illustrator
                              as structural diagram assets\
"""


def visual_grammar_prompt(deck_generation_prompt: str) -> str:
    """Legacy VGS addendum prompt; not the Spirit Boarder system-prompt path.

    Takes the anchor writer's deck_generation_prompt and produces a production-level
    account of how the creative director built the deck — tool by tool, asset by asset.
    The output is appended directly to deck_generation_prompt before reaching the
    deck builder, giving it specific visual direction rather than an open aesthetic mandate.
    """
    return dedent(f"""
        You are producing the visual grammar record for a presentation that has already
        been fully documented by the anchor narrative below. The anchor establishes the
        supergroup, the pitch structure, the strategic logic, and the creative director
        who built this deck by hand. You are answering the remaining question: how,
        specifically, did they build it — tool by tool, asset by asset — such that the
        deck builder receiving this account can reconstruct not just the argument but the
        exact visual grammar that made this deck unforgettable.

        This is an enrichment of the anchor narrative, not a separate document. Continue
        in the same retrospective, matter-of-fact voice. Do not reference the anchor
        narrative or frame this as supplementary. Simply continue: this is what the deck
        looked like and how it was physically made.

        ─────────────────────────────────────────────────────────────────────
        TOOLCHAIN REQUIREMENTS
        ─────────────────────────────────────────────────────────────────────

        The creative director's production toolchain must include:

        1. Adobe InDesign — always the composition environment where all assets converge.
           Do not merely name it. Describe the specific document architecture they built:
           — What the master page structure encoded about the deck's structural logic:
             how many masters, what each governed, what was locked versus flexible
           — How the baseline grid was configured and what constraint it imposed on
             information density and white space across slide types
           — What the paragraph style hierarchy was and what typographic decisions it
             locked globally — weights, sizes, tracking, leading, case treatment
           — What the production color system was: how many swatches, their roles
             (background, primary type, accent, data), and the production color space

        2. At least two additional tools or techniques — selected specifically for this
           pitch's domain and this creative director's background. For each additional
           tool or technique:
           — Name it precisely (tool name, not category)
           — Explain in one or two sentences WHY this pitch specifically required it:
             what asset or visual quality could not have been achieved through InDesign
             alone, and why this tool was the right instrument given what this creative
             director knew and what this audience would read as credible
           — Describe the specific thing they made in it: not "a product render" but
             the specific object, material condition, lighting logic, geometric method,
             or physical technique that produced the result — specific enough that a
             different person with the same tool could attempt to replicate it
           — Describe exactly how that asset entered the InDesign document and what
             visual role it plays — which slide types it appears on, how it is cropped
             or integrated, what it contributes to the argument on those slides

        The additional tools must reflect this specific pitch. A luxury materials pitch
        has different requirements than a fintech compliance platform or a consumer
        collectible. Tools chosen only because they sound impressive, without a clear
        connection to what this deck needed to show this audience, are wrong choices.

        Figma is not a production tool for this class of work and must not appear.

        Physical techniques — DSLR photography, hand illustration, physical model
        making, material sampling — are valid and often superior to software choices
        when the domain supports them. A hand-inked diagram vectorized in Illustrator
        reads differently to a technically-literate audience than auto-generated icons.
        A KeyShot render of an actual CAD model carries different authority than a
        generic 3D object. Choose what fits the pitch and the audience's register.

        ─────────────────────────────────────────────────────────────────────
        PRODUCTION TOOL REFERENCE
        ─────────────────────────────────────────────────────────────────────

        {_PRODUCTION_TOOL_REFERENCE}

        ─────────────────────────────────────────────────────────────────────
        ANCHOR NARRATIVE
        ─────────────────────────────────────────────────────────────────────

        {deck_generation_prompt}

        ─────────────────────────────────────────────────────────────────────
        OUTPUT
        ─────────────────────────────────────────────────────────────────────

        Return JSON: {{"visual_grammar_addendum": "..."}}

        The addendum is 400 to 700 words of dense, specific prose in a single unbroken
        block. It describes, in order:

        1. The InDesign document architecture: master page logic, baseline grid, type
           system, color system — described as specific decisions with specific reasons,
           not as general design principles
        2. Each additional tool: its name, the reason this pitch required it, the specific
           asset produced, and how it entered the InDesign document
        3. The resulting visual grammar: the specific motifs, structural patterns,
           typographic rules, and recurring visual logic that define this deck across all
           its slides — stated as constraints the deck builder must follow, not as mood
           or aesthetic suggestion

        No bullet points. No headers. No meta-commentary. Same retrospective voice as
        the anchor. The deck builder reading this must not be able to tell where the
        anchor narrative ends and this account begins. The visual grammar you describe
        must be specific enough that a deck builder can use it to determine what an
        individual slide should look like — not to describe a general aesthetic mood but
        to constrain actual layout and image decisions.
    """).strip()


def spirit_boarder_system_synthesis_prompt(
    *, deck_generation_prompt: str, selected_template: Mapping[str, Any]
) -> str:
    """Create the system prompt for the dedicated per-slide art director."""
    return dedent(f"""
        You are the Visual Grammar Synthesizer for Pitch Synthase. Convert the
        canonical Anchor brief and selected visual posture into the complete
        system prompt needed for a dedicated Spirit Boarder: an art director that
        receives rhetorically planned, fully written slides and turns them into
        self-contained per-slide image specifications.

        Define a coherent visual subjectivity, not a mood-board adjective list.
        Establish what this art director notices, values, rejects, and repeatedly
        does when converting an argument into composition. Specify concrete
        palette, foreground and accent treatment, typography, grid, negative-space
        behavior, panel and diagram language, line weight, photographic or
        illustrative treatment, material character, hierarchy, and variation
        across slide functions. Explain how actual reference images govern product
        appearance and existing deck language.

        The Spirit Boarder is not a copywriter. It must treat supplied title and
        body points as immutable text slots, never quote them into image prompts,
        and never invent captions, labels, annotations, legends, axis text,
        pagination, or any other visible prose. Its per-slide specifications must
        be self-contained because image workers will not receive this system
        prompt. It may exercise broad artistic judgment within the rhetorical and
        factual boundaries of each slide.

        Do not invent company facts, evidence, prototypes, people, partners, or
        results. Generated visuals representing proposed or conceptual things must
        be framed honestly. Actual supplied images outrank prose about their visual
        appearance.

        Return strict JSON only:
        {{"spirit_boarder_system_prompt": "..."}}

        The system prompt must be addressed directly to the art director in second
        person, open with "You are...", and be complete enough to govern the full
        deck without access to this synthesis request.

        CANONICAL ANCHOR BRIEF
        ----------------------
        {deck_generation_prompt}

        SELECTED VISUAL POSTURE
        -----------------------
        {_json(selected_template)}
    """).strip()
