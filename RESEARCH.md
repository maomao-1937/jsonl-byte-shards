# Evidence, alternatives and boundaries

Research cutoff: 2026-10-26. The batch controller inspected the primary sources
listed below before this implementation task; this project's implementation
worker used that supplied research record and did not independently reproduce
third-party issues. No competitor implementation code was copied.

## Facts and source scope

- [JSON Lines format](https://jsonlines.org/) specifies UTF-8, one valid JSON
  value per line and LF line terminators. CRLF is supported through JSON
  whitespace; a final line terminator is recommended rather than required.
  This project adds a stricter duplicate-key/nonfinite policy and hard limits.
- [Hugging Face Datasets loading documentation](https://huggingface.co/docs/datasets/en/loading)
  documents loading JSON data and multiple data files. It is a broader dataset
  ecosystem; this tool does not replace its loading, dataset transforms, remote
  access or framework integration.
- [Claude Agent SDK Python issue #1200](https://github.com/anthropics/claude-agent-sdk-python/issues/1200)
  is a reporter's account of oversized records being lost. The controller's
  research record dated the issue to August 2026. This is problem context, not
  independent confirmation of a current SDK defect, a claim about every release,
  or proof this offline file tool fixes that session path.
- [Python JSON documentation](https://docs.python.org/3/library/json.html)
  documents JSON decoding hooks and default extensions such as repeated names
  and nonfinite values. We use hooks to refuse those cases and preserve numeric
  token syntax without converting fixture numbers to floats.
- [Python OS documentation](https://docs.python.org/3/library/os.html#os.link)
  describes hard-link creation. Join uses this local-filesystem operation for
  exclusive publication of a validated sibling temporary file. Filesystems
  without the required hard-link support are outside the supported join path.

## Fair alternatives

| Alternative | Better fit | Narrow reason to use this tool |
| --- | --- | --- |
| Generic line splitters, such as GNU `split` | General text/file splitting with established Unix tooling | Need strict JSONL validation, fixed schema, per-part and combined hashes and exact round-trip checks in one small cross-platform Python file |
| Hugging Face Datasets | Loading and transforming multi-file datasets for analysis/ML | Need an offline byte-preserving fixture artifact without a dataset dependency |
| A short local script | One known fixture and a controlled environment | Need explicit malformed-input, occupied-output, tamper and hard-bound behavior with tests |

The controller's GNU manual request timed out, so no claim is made about exact
GNU flags, byte-budget packing behavior or missing capabilities. This project
does not claim a novel splitting algorithm or to outperform those alternatives.

## Inference and unknowns

Inference: bounded, hash-described local fixtures can help reproduce CI inputs
when reserializing JSON would destroy byte-level evidence. That is a design
hypothesis, not demonstrated demand. A hard oversize refusal is useful only when
the caller can choose a larger budget or handle the record separately.

Unknown: real user adoption, repository Stars, production corpus performance,
third-party SDK issue status and usefulness for any particular team. Synthetic
tests do not establish any of those. There are no paid-model calls, users,
customer claims or revenue evidence in this repository.

Verification is integrity against the supplied manifest. Provenance needs a
separately trusted digest or signature. This project has no signing, encryption,
cloud upload, session interception or data-repair feature.
