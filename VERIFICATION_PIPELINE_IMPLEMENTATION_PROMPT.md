# Implementation prompt — parallel per-slide verification pipeline

Composed using the AI Runtime Field Manual's prompt grammar (§XVIII), stacked with the Boundary-State manual's authority/assurance layer. Source tokens cited inline.

---

```text
ARTIFACT
Build a per-slide [output-validator] for the Pitch Synthase paid-deck
finalization stage, replacing the single-batch finalize_refiner_prompt call.

RUNTIME TOPOLOGY
Run it as a [DAG-runtime]: per slide, three independent verifier nodes run
in parallel, converge into one judge node, which branches to either a
terminal "pass" state or a single regeneration node. Every slide's DAG runs
independently and concurrently with every other slide's -- not a
[pipeline-runtime]; there is no shared sequential batch step across slides.

ACTIVATION
Start work per slide once its proof image and storyboard entry exist.
No [admission-controller] beyond that precondition -- every slide is
eligible the moment its proof exists.

CONTEXT
Assemble, per slide, through a [context-assembler]: the proof image, this
slide's storyboard entry (title/purpose/image_prompt -- the original
generation intent), and, for the source-accuracy verifier only, the deck's
elevator_pitch and doc_text. Do not assemble other slides' images or the
full deck into any single call -- each slide's context is self-contained.

COORDINATION
[fan-out] each slide to three narrow, single-purpose text-output verifier
calls, run in parallel:
  - spelling verifier: exhaustively enumerates every visible text element,
    checks each independently for misspelling (same discipline as
    [citation-auditor]'s claim-to-source checking, applied to letters
    instead of citations).
  - source-accuracy verifier: cross-checks every proper noun, number, and
    claim against elevator_pitch/doc_text; treats the source material as a
    [canonical-reference] for anything it settles.
  - diagram-accuracy verifier: checks internal consistency of every
    diagram, chart, icon, and arrow against what the storyboard entry says
    it should depict.
None of the three verifiers may propose a fix or regenerate anything --
verification is separate from disposition.

[fan-in] the three verifiers' issue lists into one concatenated per-slide
report. This is a plain merge, not a synthesis call -- no model call spent
on combining them.

Route the merged report to one judge/disposition call. Treat the original
storyboard entry's image_prompt as a [contract]: the judge's only job is
deciding whether the rendered slide still satisfies that contract given the
reported issues, not re-inspecting the image itself. Verdict is "pass" if
issues are empty or unresolvable-and-flagged only; verdict is "regenerate"
if at least one issue has a concrete correction.

On "regenerate", the judge writes one complete, self-contained
[semantic-prompt-compiler]-style generation prompt from scratch -- never an
image-to-image edit. It must preserve the original prompt's descriptive
detail, state every concrete correction as an explicit constraint, and
carry the deck's visual_grammar block unchanged.

STATE
No durable state beyond each slide's own proof image and (if regenerated)
final image on disk, plus the concatenated verifier reports as [traceable]
evidence per slide. No shared state is read or written across slides during
verification -- state is [ephemeral-state] to the per-slide DAG.

TIME AND RESOURCES
Verifier and judge calls are text-only (no image_generation tool) and are
NOT subject to the image [rate-limiter] -- run them at full [worker-pool]
concurrency, bounded only by ordinary API concurrency limits. Only the
conditional regeneration call for a flagged slide passes through the
existing [concurrency-limiter] (bounded chunks) and [rate-limiter]
(5 calls / rolling 60s) that already governs all image_generation-tool
calls in this codebase.

SIDE EFFECTS
The only side effect requiring the [rate-limiter] is a new image write, and
only for slides the judge actually flags -- a slide that passes produces no
new image at all; its existing clean proof is used as-is as the final.

FAILURE
Per-slide, per-verifier-type isolation: a parse failure or error on one
verifier call, one slide, or one judge call must not affect any other
slide's pipeline. If a slide's proof is missing entirely, drop that slide
from the composited output (log it) rather than failing the deck -- same
policy already implemented for the prior mechanism. No single point of
failure exists across the whole deck the way the old batch
finalize_refiner_prompt call was.

POLICY
Cross-slide style coherence is explicitly out of scope for this mechanism
-- deferred, would require a heavier call taking every slide image as
input. Do not build it as part of this pass.

EVIDENCE
Return an [acceptance-manifest]: for every slide, which verifiers ran,
every issue found (verifier, location, problem, correction), the judge's
verdict and reasoning, and whether the shipped final image is the original
proof or a regeneration. This is the direct replacement for the old
single-call finalize_refiner output, now [traceable] to a specific
verifier and slide rather than one opaque batch decision.

AUTHORITY AND TARGET STATE
Treat each slide's original storyboard image_prompt as the [contract]
governing that slide, and elevator_pitch/doc_text as the [canonical-
reference] for factual content.
Deliver the finalization stage [verified]: every slide's rendered content
is checked against its own generation contract and against the real
source material, with discrepancies recorded, not merely polished.
Return the [acceptance-manifest] described above as the evidence artifact.
```
