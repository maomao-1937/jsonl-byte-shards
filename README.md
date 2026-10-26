# jsonl-byte-shards

[![tests](https://github.com/maomao-1937/jsonl-byte-shards/actions/workflows/tests.yml/badge.svg)](https://github.com/maomao-1937/jsonl-byte-shards/actions/workflows/tests.yml)

Split a local JSONL fixture into byte-bounded parts, verify every part, then join
the **exact original bytes**. LF, CRLF, UTF-8 characters, number spellings and an
optional missing final newline survive the round trip. JSON is validated, never
reserialized. A record larger than the budget is rejected whole.

Useful when a reproducible test fixture needs bounded files and a checkable
manifest. No services, upload, compression, dependencies or API keys.

[中文快速上手](README.zh-CN.md) · [Research and alternatives](RESEARCH.md) ·
[Validation evidence](TESTING.md)

## Install and run

Requires Python 3.11 or later. Download this repository and run the script from
its directory; no package installation is needed. `python --version` should
report 3.11+. On Windows, `py -3.11` can replace `python`.

```console
python jsonl_byte_shards.py --help
python jsonl_byte_shards.py split examples/sample.jsonl --out-dir sample-parts --part-bytes 45
python jsonl_byte_shards.py verify sample-parts/manifest.json
python jsonl_byte_shards.py join sample-parts/manifest.json --out restored.jsonl
```

`sample-parts` and `restored.jsonl` must not exist, and their parent directories
must already exist. To repeat a demo without managing output paths:

```console
python examples/demo.py roundtrip
python examples/demo.py tamper
python examples/demo.py occupied
python jsonl_byte_shards.py split examples/duplicate-key.jsonl --out-dir invalid-parts --part-bytes 100
```

The last three commands deliberately exit 1, 2 and 2 respectively. The wrappers
use a fresh temporary directory each time. They never delete user output paths.

## Input and output contract

Input is a nonempty UTF-8 file containing one JSON value per LF-delimited record.
Objects, arrays, strings, numbers, booleans and null are supported. Duplicate
object keys (also nested), BOM, blank records, invalid UTF-8 and NaN/Infinity
constants are refused. JSON number tokens are syntax checked without floating
point conversion: `1e999999` is valid JSON syntax and retains its bytes.
CRLF is accepted; the last record may lack LF. Parser nesting limits still apply.

Greedy packing adds complete records until the next record would exceed the
budget. Every nonfinal part ends in LF. Files are named `part-00001.jsonl`,
`part-00002.jsonl`, etc. The manifest contains no source machine path:

```json
{
  "format_version": 1,
  "source": {"bytes": 6, "records": 3, "sha256": "<64 lowercase hex characters>"},
  "part_bytes": 4,
  "parts": [
    {"filename": "part-00001.jsonl", "bytes": 4, "first_record": 1,
     "last_record": 2, "sha256": "<64 lowercase hex characters>"},
    {"filename": "part-00002.jsonl", "bytes": 2, "first_record": 3,
     "last_record": 3, "sha256": "<64 lowercase hex characters>"}
  ]
}
```

This is a schema illustration, not a valid fixture: replace the hash placeholders
with actual hashes. Integer fields reject booleans; unknown/missing fields,
duplicate JSON keys, unsupported versions and filenames/order are rejected.
No absolute paths, traversal or nested part paths are accepted. Verification
opens only the listed files, so unlisted directory contents are outside scope.

`verify` reparses the actual records and checks actual part sizes, complete record
boundaries, greedy packing, contiguous one-based line ranges, individual SHA256,
combined byte/record counts and combined SHA256. `join` uses the captured bytes
from this verification, stages the result, checks its hash and publishes a new
file. It does not blindly reread the parts after verification.

Success writes a single JSON summary with the action, counts and SHA256. Errors
report fixed reasons and codes. Neither path prints record contents or parser
snippets. SHA256 identifies contents; it does not hide predictable data.

## Hard bounds and exit codes

| Bound | Maximum |
| --- | ---: |
| Source / actual combined parts | 64 MiB |
| One record, including CR/LF bytes | 1 MiB |
| Records | 100,000 |
| Parts | 1,000 |
| Manifest | 1 MiB |
| `--part-bytes` | 1 through 16 MiB |

MiB means 1,048,576 bytes. Manifest declarations and actual data are bounded.
Processing uses bounded memory rather than supporting an unbounded stream;
multiple copies of the capped data and Python JSON objects may coexist, so the
64 MiB input cap is **not** a 64 MiB process memory cap.

| Exit | Meaning |
| --- | --- |
| 0 | Split completed, manifest verified, or join completed |
| 1 | Structurally supported manifest disagrees with actual content, hashes, packing or record ranges |
| 2 | Invalid split input, malformed/unsupported manifest, hard bound, occupied output, unsafe file type/path, or filesystem/report I/O/setup failure |

Missing/unreadable part files are filesystem failures (2). Invalid JSON *inside*
an otherwise supported manifest's parts is an integrity failure (1). Hard bounds
take precedence over integrity mismatches. No partial join is published if
verification fails.

The stdout summary is flushed before returning. A summary write/flush failure
(including a closed stdout pipe) returns 2 even if split or join has already
published a complete, valid output. Report failure does not roll back or delete
published files; input and existing artifacts remain unchanged. Inspect or verify
the requested output before choosing another path, since rerunning against an
already published path will be refused. A receiver may obtain a partial summary.

## Filesystem and trust boundaries

- Any existing output path is refused, including a directory, a dangling symlink
  or a hard link. Input bytes and existing files are never modified.
- Observed input/source/manifest/part paths and ancestors reject symlinks and
  Windows reparse points. Input files must be regular files with one hard link.
- Split stages and verifies its own files before creating the output directory.
  It publishes parts with exclusive creation and the manifest last. A publication
  I/O failure may leave an incomplete new directory: **directory publication is
  not transactional**. Inspect that new directory before choosing another path.
- Join uses a private sibling staging file and an exclusive hard-link publication,
  then removes its staging name. This requires local filesystem hard-link support;
  lack of support returns 2. No rename can replace an occupied output.
- These are local offline tools for files that remain immutable during each run.
  Observed path checks and file metadata checks are not an adversarial race-proof
  filesystem sandbox, and success does not promise power-loss durability.
- A successful verification establishes internal integrity relative to the
  **supplied manifest**. Someone who replaces both data and manifest can create
  another valid bundle. Use a separately trusted hash/signature for provenance.

This tool does not split one huge logical record, repair truncated JSON, capture
session streams, solve SDK context limits, or certify a dataset's meaning.

## Tests

```console
python -m unittest discover -s tests -v
```

`checks.json` lists repeatable CLI examples and expected exit codes. Tests use
synthetic data and temporary output directories. Symlink tests explicitly skip
when Windows does not grant symlink creation; that skip is not evidence that
those cases passed on that machine. MIT license, copyright 2026 maomao-1937.
