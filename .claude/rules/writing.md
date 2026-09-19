# Writing

Prose in this repository is Simplified Technical English (ASD-STE100). Apply
it to documentation, commit messages, pull request text, comments, docstrings,
and error and log strings. Do not apply it to code, identifiers, or command
syntax. The `ste-writing` skill holds the full method for rewriting a text.

## Strict mode

Comments, docstrings, error strings, log strings, and changelog entries follow
every rule below.

- Active voice. "The parser reads the file", not "the file is read".
- One instruction per sentence. At most 20 words in an instruction, 25 in a
  description.
- No semicolon. Write two sentences.
- No contraction.
- Use a verb for an action. "Analyze the log", not "perform an analysis".
- One name for one thing. Do not call the same item by two names.
- The short common word: start, use, help, make sure, before, after, about,
  get, show, also.
- No marketing adjective: robust, seamless, powerful, and their kind.
- American spelling.

## Comments

- Imperative mood. "Return the parsed chunk", not "Returns the parsed chunk".
- No history. A comment describes the code as it is now. What changed, and
  why, belongs in the commit message.
- Intent, not mechanics. Say why the code exists or what contract it keeps.
  Do not restate what the code says.
- No `TODO` without a reason for the delay.
- No attribution. No name, no date. `git blame` holds that.
- No commented-out code.

## Commit messages

- Subject: one imperative sentence, under 72 characters, no type prefix. The
  log reads as a list of what each change does.
- Body: what happened, what was measured, and why this shape. Say what the
  defect cost when there was one. End with `Closes #N` when a commit closes
  an issue.
- A changelog is generated from the bodies, so write the body well.

## Self-check before returning text

1. A sentence over 20 words? Split it.
2. A semicolon? Two sentences.
3. A contraction? Expand it.
4. Passive voice with a known actor? Make it active.
5. A nominalization or a phrasal verb? A plain verb.
6. The same thing under two names? One name.
7. An identifier changed? Restore it.
