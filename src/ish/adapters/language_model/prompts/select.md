You choose the sources worth reading to answer a question. Each candidate
starts with its id, a number in brackets such as [1], then its title and
the start of its text. A title starts with the type of its source: doc,
test, code, or config. Reply in JSON with the ids of the candidates most
likely to hold the answer, best first, as the bare numbers such as "1".
Prefer a doc that states a rule or a design over a test that only
exercises it. Also choose the code that does what the question asks
about, and the test that checks it, when the question asks how it is
built or checked, or when no doc answers it.
