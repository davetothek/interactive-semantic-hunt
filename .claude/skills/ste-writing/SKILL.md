---
name: ste-writing
description: >-
  Rewrite technical prose in ASD-STE100 Simplified Technical English to remove
  "AI slop". Applies to AsciiDoc specifications and standards, markdown
  documentation, READMEs, build guides and rule files, commit bodies,
  pull-request text, release notes, error and log strings, and code comments.
  Never applies to code, identifiers, symbol names, or command syntax. Use it
  when asked to make writing clear or plain, to make text not sound like AI,
  or to check a text against the house style.
---

# ste-writing — rewrite a text

The rules live in `.claude/rules/writing.md`, and they load every session.
This skill is the procedure for applying them to a text that exists, or for
writing one that must pass them.

## 1. Pick the mode

- **Strict**: procedures, runbooks, safety text, error and log strings,
  comments and docstrings, changelog entries, specifications and standards.
  Every rule, both length caps, the small dictionary.
- **STE-flavored**: READMEs, guides, pull-request text, commit bodies, rule
  and skill files. The sentence, paragraph, voice, and plain-verb rules.
  The dictionary relaxes so the text keeps its range.

A specification tree that holds source text in another language: write the
English only. Do not translate or rewrite the source.

## 2. Read the text once for meaning

Before touching a sentence, say in one line what the text is for and who
reads it. A rewrite that loses the point is worse than the slop it removed.

## 3. Rewrite, sentence by sentence

For each sentence, in this order:

1. Find the actor and the verb. Make the actor the subject.
2. Cut a stacked auxiliary: "it is important to note that this may help to
   improve" becomes "this improves".
3. Replace a nominalization with its verb, and a phrasal verb with a plain
   one.
4. Split at 20 words for an instruction, 25 for a description.
5. Replace a semicolon with a period.
6. Expand a contraction.
7. Replace a marketing adjective with the fact it was hiding, or delete it.
8. Check that one thing keeps one name across the whole text.

Keep every identifier, path, flag, and command exactly as it was.

## 4. Check the structure

One topic per paragraph, at most six sentences. Steps go in a numbered list,
one action each, imperative, with the condition before the command: "When
the tree is dirty, stop."

## 5. Run the self-check

The list at the end of `.claude/rules/writing.md`. Run it before returning
the text, not after.

## What wins over style

- Do not hand-edit a generated file.
- Where a formatter or a spell checker runs in a hook, do not hand-format.
- A marker in a specification stays terse: `_(TODO: ...)_` in AsciiDoc.

The standard is copyrighted. Do not paste it. <https://asd-ste100.org>
