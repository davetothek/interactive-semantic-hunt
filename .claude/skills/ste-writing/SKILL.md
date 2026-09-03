---
name: ste-writing
description: >-
  Write or rewrite technical prose in ASD-STE100 Simplified Technical English to remove
  "AI slop". Applies to AsciiDoc specifications and standards, markdown documentation,
  READMEs, build guides and rule files, commit bodies, pull-request text, release notes,
  error and log strings, and code comments. Never applies to code, identifiers, symbol
  names, or command syntax. Use this skill when asked to make writing clear or plain,
  to make text not sound like AI, to enforce a controlled writing style, or to write
  any documentation in a project that has adopted STE. Two modes — strict (procedures,
  safety text) and STE-flavored (general prose).
---

# ste-writing — Simplified Technical English

Write prose in ASD-STE100 Simplified Technical English. STE removes voice on purpose.
Do not use it for marketing copy or for text that needs a voice.

The rules below are the whole standard you need. Apply them, then run the self-lint.

## Rules

WORDS

- Use one name for one thing. Do not call the same item by two different names.
- Use the short common word: start (not begin/commence/initiate), use (not utilize/
  leverage), help (not facilitate), make sure (not ensure), before (not prior to),
  after (not subsequent to), about (not regarding/concerning), get (not obtain/
  acquire), show (not demonstrate), also (not additionally/furthermore/moreover).
- Give each word one meaning. "fall" means to move down, not to decrease.
- No marketing adjectives: seamless, robust, powerful, cutting-edge, effortless,
  world-class, next-generation, revolutionary.
- American spelling.

VERBS

- Active voice. "the parser reads the file", not "the file is read by the parser".
- Use a verb for an action. "analyze the log", not "perform an analysis of the log".
- No stacked auxiliaries. Not "it is important to note that this may help to
  improve". Write "this improves X".
- No "-ing" main verb where a simple tense works.

SENTENCES

- One instruction per sentence. Max 20 words (instruction), max 25 (descriptive).
- No contractions. Use articles: a, an, the, this, these.

PUNCTUATION

- No semicolons. Write two sentences. The em dash is allowed. STE bans only the
  semicolon.

STRUCTURE

- One topic per paragraph, max six sentences. For steps, use a numbered vertical
  list, one action per item, imperative form. Put a condition before its command.

Write only the requested text. No preamble, no summary, no closing remarks.

## Modes

- **strict** — procedures, runbooks, safety text, error and log strings, and any
  functional-safety evidence text: apply every rule and both length caps.
- **STE-flavored** — general prose (READMEs, pull-request text, guides): apply the
  sentence, paragraph, active-voice, and plain-verb discipline. Relax the ~900-word
  dictionary lockdown so the text keeps enough range to read naturally.

## Where this applies

Pick the mode from the kind of text, not from the file extension:

- Specifications and standards — strict. Where a specification tree holds source text
  in another language, write the English text only. Do not translate or rewrite the
  source text.
- Internal documentation — strict for a procedure or a standard, STE-flavored for the
  rest.
- READMEs, build guides, and agent rule and skill files — STE-flavored.
- Commit bodies and pull-request text — STE-flavored. The Conventional Commit subject
  keeps its own form.
- Changelogs and release notes — strict. A changelog generator derives the entries
  from the commit bodies, so write the commit body well.
- Code comments, error strings, and log strings — strict. Comment the reason, not the
  syntax.
- Code, identifiers, symbol names, and command syntax — not applicable.

Project rules that win over style:

- Do not hand-edit a generated file.
- Keep specification markers terse. Use `_(TODO: ...)_` in AsciiDoc. Do not add inline
  comment blocks.
- Where pre-commit runs a formatter or a spell checker, do not hand-format. Let the
  hooks normalize the change.

## Self-lint (run before you return text)

1. Any sentence over 20 words? Split it.
2. Any semicolon? Replace it with a period.
3. Any contraction? Expand it.
4. Any passive voice with a known actor? Make it active.
5. Any "-ing" main verb, nominalization ("perform an analysis"), or phrasal verb
   ("spin up")? Replace it with a plain verb.
6. Same thing named two ways? Pick one name.
7. Any identifier or symbol name changed? Restore it.

The rules above are lintable, and they remove the form of slop. Full STE also needs
human judgment: the right technical noun, and whether a sentence makes good sense. A
checker cannot certify that. This skill cannot make a hollow paragraph true.

The standard is free (do not paste it in full, it is copyrighted):
<https://asd-ste100.org>
