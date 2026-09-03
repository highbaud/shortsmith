# Manual person photos

Photos for people Wikidata has no usable free image for. Everything else in
`assets/people/` is a re-fetchable Wikidata/Commons cache and stays out of git;
these are tracked because a fresh clone cannot rebuild them, and without them
these people get no b-roll cutaway at all.

`gen_broll._manual_person_photo()` looks a file up by slug, so the basename must
stay `<lowercase name, no spaces or punctuation>`. Each image has a `.json`
sidecar recording where it came from.

## Attribution

These are third-party editorial photographs, included here by the repository
owner's decision. They are **not** covered by this project's MIT license, and
each is credited to its source below. If you are a rights holder and want an
image removed, open an issue and it will be taken out.

| File | Person | Source | Stated license |
|---|---|---|---|
| `davidschwartz.jpg` | David Schwartz | [https://www.theblock.co/profile/313826/david-schwartz](https://www.theblock.co/profile/313826/david-schwartz) | unknown; third-party editorial photo supplied by the operator |
| `jedmccaleb.png` | Jed McCaleb | [https://stellar.org/foundation/team](https://stellar.org/foundation/team) | unknown; Stellar Development Foundation team photo supplied by the operator |
