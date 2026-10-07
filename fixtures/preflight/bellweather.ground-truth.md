# Bellweather Preflight Fixture: Ground Truth

This companion file is for evaluation only. Do not give it to the preflight system.

## Design

This is a representative control fixture, not an adversarial fixture. Most evidence is conventional English narrative prose and is deliberately redundant. A few mild ambiguities occur naturally.

## Definite characters

- Jonah Hart: first-person narrator. Addressed as Jonah and explicitly as Jonah Hart.
- Mara Venn: major present-day character. Usually Mara. Supported she/her evidence.
- Evelyn Hart: major present-day character, Jonah's aunt. Evelyn and Aunt Evelyn. Supported she/her evidence.
- Daniel Rook: major present-day character. Usually Daniel. Supported he/him evidence.
- Rose Calder: major present-day character. Usually Rose. Supported she/her evidence.
- Tomas Reed: major present-day character. Usually Tomas. Supported he/him evidence.
- Lillian Hart: Jonah's deceased mother. Also Lillian and my mother. Real person despite no present-day dialogue. Supported she/her evidence.
- Eleanor Bell: historical person. Eleanor Bell, Eleanor, E. Bell, and Miss Bell in archival material. Supported she/her evidence.
- Samuel Ward: historical person. Samuel Ward and S. Ward. Supported he/him evidence.
- Silas Bell: historical person and observatory builder. Sparse but genuinely a person.
- Mr. Vale: mentioned person. Sparse. It is acceptable for a conservative first-pass system to miss him.

J. Mercer and A. Pike appear only as names written on an archival photograph. They may be real people in the story world, but there is intentionally too little evidence to expect confident character promotion.

## High-value alias relationships

- Mara Venn -> Mara
- Evelyn Hart -> Evelyn; Aunt Evelyn
- Daniel Rook -> Daniel
- Rose Calder -> Rose
- Tomas Reed -> Tomas
- Lillian Hart -> Lillian
- Eleanor Bell -> Eleanor; E. Bell; Miss Bell
- Samuel Ward -> S. Ward
- Jonah Hart -> Jonah

Missing an alias is not automatically a defect. Some require more inference than a conservative deterministic pass may support.

## Non-characters useful for false-positive checks

Bellweather; Harrowgate; Orchard Street; Market Square; Bellweather Observatory; Bellweather Historical Society; Historical Society; North Vale Development; Vale & Son Hardware; St. Anne's church; Bell & Crown; Cambridge; Draconid; Water; Rain; Thunder; Sunday; Friday; Monday; October.

## Mild natural ambiguities

Rose is genuinely a person even though "rose" is also an ordinary English word.

Bell occurs as a surname, inside Bellweather, in Bell Number Three, and in Bell & Crown.

Vale occurs in Mr. Vale, North Vale Development, and Vale & Son Hardware.

Archival passages contain abbreviated personal names. This is ordinary source material rather than an edge-case trap.

## Baseline questions

1. How many definite characters are discovered?
2. Which major repeated characters are missed?
3. Which non-characters are falsely promoted?
4. Which aliases are discovered?
5. Is pronoun/gender evidence actually supported by the text?
6. How large is the artifact relative to the source?
7. How long does preflight take?
8. Does block extraction preserve the Markdown structure sensibly?

A useful conservative baseline should discover several major repeatedly evidenced characters, especially Mara, Evelyn, Daniel, Rose, Tomas, and likely Eleanor. Perfect recall is not required. Avoiding a large junk cast is more important than discovering every sparse historical or mentioned person.

Do not tune the implementation merely to make this fixture perfect. It exists to establish a normal baseline before a full-length EPUB and later adversarial testing.
