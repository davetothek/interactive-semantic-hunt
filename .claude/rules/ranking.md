---
paths:
  - "src/ish/application/ranking.py"
  - "src/ish/adapters/vector_store/**"
---

# Ranking

- The ranking policy lives once, in `application/ranking.py`. A store
  supplies two primitives, `_semantic()` and `_lexical()`, and nothing else
  about ranking.
- The result filter runs inside the primitives, before the top slice. A
  filter applied after the slice starves a narrow filter.
- Keep the `is_code_like` gate. The lexical half runs only for a query that
  names something: an underscore, an all-capital word, or mixed case.
  Fusing a lexical order into a plain description cost 10 points of top-1
  accuracy.
- Run `/benchmark` before any change to `SEMANTIC_WEIGHT`, `LEXICAL_WEIGHT`,
  `RRF_K`, the gate, or the fusion. Put the table in the commit body.
- The reported score is the cosine similarity, whether or not the lexical
  half ran.
- Over-fetch only when something will trim: a filter, or the lexical half.
- The lexical index holds `symbol` and `terms` only, never the body.
