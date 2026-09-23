// Decide when a query is worth completing, and put a choice into it.
//
// The server finishes the word and names the candidates. This module
// only knows the three keys, so it can tell a filter word in progress
// from an ordinary word without a round trip, and can write a chosen
// candidate back into the query.

/** The filters a query may carry. The server reads these same words. */
export const KEYS = ["lang:", "type:", "under:"] as const;

/** The last word of *text*, which is what completion works on. */
export function lastWord(text: string): string {
  const at = text.lastIndexOf(" ");
  return at < 0 ? text : text.slice(at + 1);
}

/**
 * Whether the last word of *text* could be a filter word.
 *
 * True for a prefix of a key, such as `ty`, and for a key with its
 * value under way, such as `lang:cp`. False for an empty word and for
 * a word no key opens with, so an ordinary query costs no extra call.
 */
export function isFilterWord(text: string): boolean {
  const word = lastWord(text);
  if (word === "") {
    return false;
  }
  return KEYS.some((key) => key.startsWith(word) || word.startsWith(key));
}

/**
 * Replace the last word of *text* with *candidate*.
 *
 * A candidate for a value is written after its key, and a finished
 * value takes a space so the next word starts clean. A candidate that
 * is a key itself takes no space, because the value goes straight
 * after the colon.
 */
export function takeCandidate(text: string, candidate: string): string {
  const word = lastWord(text);
  const head = text.slice(0, text.length - word.length);
  const colon = word.indexOf(":");
  if (colon >= 0 && KEYS.includes(word.slice(0, colon + 1) as (typeof KEYS)[number])) {
    return `${head}${word.slice(0, colon + 1)}${candidate} `;
  }
  return `${head}${candidate}`;
}
