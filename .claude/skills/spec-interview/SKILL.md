---
name: spec-interview
description: "Run a structured requirements interview BEFORE writing any code, producing a spec.md that becomes the ground truth for the work. Use this whenever the user is starting a new project or a substantial new feature and the requirements are still fuzzy — phrases like 'I want to build…', 'help me set up a new project', 'before we start coding', 'let's spec this out', 'interview me', or any request where jumping straight to code would risk building the wrong thing. Prefer this over immediately scaffolding code when the goals, users, or scope are underspecified."
---

# Spec Interview

Before writing code, interview the user until you genuinely understand what they want — then write it down as a `spec.md` that everything downstream (planning, tasks, implementation) is grounded in.

The failure mode this prevents is **drift**: confident, plausible code that quietly solves the wrong problem because nobody pinned down the requirements first. The cheapest place to fix a misunderstanding is in a conversation, not in a diff. A short interview up front routinely saves hours of building the wrong thing.

**Flow at a glance:**
1. Detect the mode (greenfield vs. existing codebase) — Step 0 below.
2. Interview through buckets 1–7, in order, one question at a time.
3. Write `spec.md` from the template.
4. Show it to the user and get sign-off.
5. If the user wants to proceed, break the spec into a task list (see "Breaking it down").

## The core method

**Ask one question at a time.** Not a wall of questions — one focused question, wait for the answer, and let that answer shape the next question. This is how you surface what the user *actually* needs versus what they *first say* they need. People rarely hand you complete requirements; you draw them out.

**Work through buckets in order.** The interview is organized into the buckets below. Finish one bucket before moving to the next, so both you and the user are focused on one part of the problem at a time. At the end of each bucket, give a 1–2 line recap of what you heard and confirm it before moving on ("So far: <recap>. Did I get that right?"). This lets the user course-correct per-section instead of discovering a misunderstanding at the very end.

**Stop when the spec is buildable, not when you're bored.** The test: could another engineer start building from the spec without needing to come back with more questions? If yes, you're done. Concrete stop rules:
- If a bucket is already obviously answered by what the user told you, skip it — don't ask questions whose answers you already have.
- Ask at most 2–3 questions per bucket before moving on; record anything still unclear as an assumption in **Open questions / assumptions** rather than grinding.
- If the user answers "I don't know" twice in a bucket, stop probing that bucket — propose a sensible default, note it as an assumption, and move on.
- Depth should match the work: a weekend script needs a light pass (often 5–6 questions total); a system others will depend on needs the full set of buckets.

**Escape hatches — always honor these.** If the user says "just build it," "skip the interview," "I don't know, you decide," or similar, stop interviewing. Make reasonable assumptions, write them into the spec's **Open questions / assumptions** section clearly flagged, and proceed. The interview serves the user; it is not a gate they have to pass.

## Step 0 — Detect the mode

First, figure out which situation you're in, because it changes the questions:

- **Greenfield** — a brand-new project from scratch. You'll ask about stack direction, project setup, and the shape of the whole thing.
- **Existing codebase** — a new feature or change inside code that already exists. Before interviewing, look at the relevant code (read the files, check the structure) so your questions are informed. Focus shifts to integration points and *what must not change*.
  - If a knowledge graph already exists for this repo (a `graphify-out/` directory is present, or graphify query tools are available), use it to map structure, dependencies, and integration points quickly — it's faster than reading files one at a time and surfaces what-must-not-change well. But don't build a graph just for the interview; if none exists, reading the relevant files directly is the right call.

If it's ambiguous, ask. Don't assume greenfield just because the user is excited about something new.

## The buckets

Adapt wording to the user and the mode. These are the areas to cover, not a script to read verbatim.

### 1. Context & problem
- What are we building, in one or two sentences?
- What problem does it solve, and who has that problem today? How do they cope now?
- Why now / why does this matter?
*(Existing-code mode: also — where does this live in the current system, and what triggered the need?)*

### 2. Users & goals
- Who are the users? (Be concrete — roles, one real example.)
- What's the primary job they're trying to get done?
- What does success look like *for them*?

### 3. Scope & user stories
- Walk through the core flows as user stories: "As a <user>, I want <capability> so that <outcome>."
- What's explicitly **in** scope for the first version?
- What's explicitly a **non-goal** (out of scope, at least for now)? Non-goals are as valuable as goals — they prevent scope creep.

### 4. Behavior & acceptance criteria
For the key stories, get concrete about behavior. Use **EARS notation** — it's unambiguous and testable:
> **WHEN** [trigger / condition] **THEN** the system **SHALL** [observable behavior].

For example:
> WHEN the user submits the form with an empty email field THEN the system SHALL show an inline error and SHALL NOT send the request.

Also probe edge cases and failure behavior: What happens when input is bad? When something's empty, missing, or at the limit? What should *not* happen?

### 5. Constraints & integration
- Tech stack — decided, or open? Any hard requirements (language, framework, platform, deployment target)?
- Dependencies, data sources, external services, APIs?
- *(Existing-code mode, critical:)* **What must not change?** Which existing behaviors, APIs, data shapes, or files are off-limits? What would break other things if touched?
- Non-functional constraints: performance, scale, security, privacy, offline, budget.

### 6. Boundaries (Always / Ask-first / Never)
Establish a three-tier guardrail so the implementation stays inside the lines:
- **Always** — things the implementation should always do (e.g. run tests before declaring done, follow existing code style).
- **Ask first** — actions to check with the user before doing (e.g. adding a new dependency, changing the data schema, deleting files).
- **Never** — hard boundaries (e.g. never touch secrets/credentials, never push to main, never modify <specific files>).

### 7. Success metrics & done
- How will we know it works? What's the acceptance/verification approach (manual check, tests, a demo flow)?
- What's the definition of "done" for the first version?

## Writing the spec

When the spec is buildable (per the stop rules above) or you've hit an escape hatch, write **`spec.md`** in the project root using this template. Keep it tight — enough nuance to guide the work without overwhelming it. Fill sections from the interview; drop a section only if it's genuinely N/A, and note why.

```markdown
# <Project / Feature name> — Spec

## Overview
<1–3 sentences: what this is and the problem it solves.>

## Users & goals
- **Users:** <who>
- **Primary job:** <what they're trying to accomplish>
- **Success for the user:** <what good looks like from their side>

## User stories
- As a <user>, I want <capability> so that <outcome>.
- …

## Scope
**In scope (v1):**
- …

**Non-goals:**
- …

## Acceptance criteria
- WHEN <condition> THEN the system SHALL <behavior>.
- …

### Edge cases & failure behavior
- <case> → <expected behavior>

## Constraints
- **Stack / platform:** <decided or open>
- **Dependencies / integrations:** <…>
- **Must not change:** <existing behavior/files that are off-limits — existing-code mode>
- **Non-functional:** <perf / security / privacy / scale / budget>

## Boundaries
- **Always:** <…>
- **Ask first:** <…>
- **Never:** <…>

## Success metrics & definition of done
- <how we'll verify it works; what "done" means for v1>

## Open questions / assumptions
- <unresolved questions, and any assumptions made under an escape hatch — flagged clearly>
```

Treat `spec.md` as a **living document**: if decisions change during planning or implementation, update it so it stays the ground truth rather than going stale.

## After the spec

Show the user the spec and get a quick sign-off. Then ask whether they want to proceed to planning/implementation — don't silently start coding. The spec is the handoff; confirm the handoff before acting on it.

## Breaking it down

Once the spec is signed off and the user wants to proceed, derive a **task breakdown** from it before writing code. This lives downstream of the spec — don't let it leak into the interview or into `spec.md` itself, which stays a requirements document.

How to build the list:

1. Go through the **Acceptance criteria** in the spec, top to bottom. For each criterion, write one or more tasks that implement it. Then add any setup/plumbing tasks the criteria imply (project scaffolding, schema, wiring).
2. Make every task **bite-sized**: it has one clear outcome and a stated verification ("done when: <check>"). If you can't state how a task will be verified, split or sharpen it until you can.
3. Check coverage in both directions: every acceptance criterion is covered by at least one task (a criterion no task covers is a gap), and every task traces back to something in the spec (a task that maps to nothing is scope creep — flag it to the user, don't smuggle it in).
4. Order the list so a minimal end-to-end path works early — first make the simplest possible version run all the way through, then add features and polish. Put risky or uncertain tasks before the tasks that depend on them.
5. Record the list with the harness's native task tracking (e.g. TaskCreate) so progress is visible as you work; optionally also write a `tasks.md` next to `spec.md` if the user wants a durable artifact.

Each task should look like:

> **Task:** Add email validation to the signup form
> **From spec:** acceptance criterion "WHEN the user submits with an empty email…"
> **Done when:** submitting an empty email shows the inline error and no request is sent (manual check + unit test).

If a task's scope changes during implementation, reconcile it against the spec — and update `spec.md` if the *requirement* changed, not just the task.
