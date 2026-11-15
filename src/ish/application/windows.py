"""Divide a long chunk of a document into windows, each embedded on its own.

One vector stands for one chunk, and an embedding model averages what
the chunk says. A section of a specification says many things, so its
vector stands for the topic of the section and not for any one sentence.
Measured on a specification tree: a section of 4,075 characters scored
0.572 for a query that quotes one of its sentences nearly word for word,
below 56 other chunks. A window of about 400 characters around that
sentence scored 0.722, above the best chunk of the index at 0.644.

A window is an extra vector of its chunk. A search scores every vector
and gives a chunk the best score of its own and its windows, so a
result and an answer still read the whole section.
"""

# Characters a window holds, about. Enough for a sentence or two and a
# short list, with the heading path of the section in front.
WINDOW_CHARS = 400


def windows(text: str, size: int = WINDOW_CHARS) -> list[str]:
    """Return *text* as windows of whole lines, about *size* characters each.

    Return no window for a text of fewer than twice *size* characters:
    its own vector already stands for each of its few sentences. Skip
    empty lines. Start each window with the last line of the window
    before it, so a sentence that a break divides stands whole in one of
    them. A line longer than *size* is a window by itself.
    """
    if len(text) < 2 * size:
        return []
    lines = [line for line in text.splitlines() if line.strip()]
    found: list[str] = []
    held: list[str] = []
    size_held = 0
    for line in lines:
        if held and size_held + len(line) > size:
            found.append("\n".join(held))
            held = [held[-1]] if len(held) > 1 else []
            size_held = sum(len(kept) for kept in held)
        held.append(line)
        size_held += len(line)
    if held and (not found or held != [found[-1].splitlines()[-1]]):
        found.append("\n".join(held))
    return found
