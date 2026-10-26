# Validation evidence

Run date: 2026-10-26 (Asia/Singapore). Local environment: Python 3.11.15,
Microsoft Windows NT 10.0.26300.0, x86-64. Standard library only.

## Test-first observations

1. Before creating `jsonl_byte_shards.py`, `python -m unittest discover -s tests -v`
   ran 14 cases and failed 11 because successful CLI operations were absent.
   Negative cases returning 2 during that missing-script run were not treated as
   implementation evidence.
2. The first implementation exposed an arithmetic mistake in the Unicode/CRLF
   fixture's expected budget: its first record was 13 bytes. The test budget was
   corrected from 15 to 16 to exercise the intended greedy two-record first part.
3. A separate regression test patched only the low-level open boundary to assert
   that nonregular inputs are rejected before opening. It failed before the
   pre-open file-type check and passed after that check was added.

## Final commands and outcomes

```console
python -m unittest discover -s tests -v
```

Final source-only temporary-copy run: **24 cases; 23 passed, 1 skipped**.
The skipped symlink test reported `WinError 1314` (symlink creation privilege
unavailable). No claim is made that this Windows run exercised actual symlink
or junction/reparse-point rejection. Existing hard-link input/output tests did
run and pass. Linux CI can exercise the symlink branch when available.

The final validation copied source files into a new temporary directory, excluding
`.git`, `__pycache__`, `*.pyc` and `.venv`; it ran unittest discovery there and
then launched every `checks.json` argument list with `sys.executable`:

| Command after `python` | Expected / observed exit |
| --- | --- |
| `jsonl_byte_shards.py --help` | 0 / 0 |
| `examples/demo.py roundtrip` | 0 / 0 |
| `examples/demo.py tamper` | 1 / 1 |
| `examples/demo.py occupied` | 2 / 2 |
| `jsonl_byte_shards.py split examples/duplicate-key.jsonl --out-dir invalid-parts --part-bytes 100` | 2 / 2 |
| `jsonl_byte_shards.py split examples/sample.jsonl --out-dir invalid-parts --part-bytes 0` | 2 / 2 |

The wrappers create and clean only their own temporary directories. The last two
commands reject before creating `invalid-parts`, so rerunning checks is safe.

## Exercised behavior

- Exact original bytes and deterministic manifests across separate outputs:
  UTF-8 Unicode, CRLF, LF and missing final LF; all JSON value types; long number
  lexemes validated without conversion.
- Greedy complete-record budgets; actual record count/record size/part count;
  source files above 64 MiB, parts above 16 MiB, manifests above 1 MiB and invalid
  part budgets. Large byte tests use bounded temporary sparse/truncated files.
- Per-part and combined hashes/sizes, record range metadata, invalid JSON even
  with recomputed hashes, missing nonfinal LF and non-greedy forged packing.
- Unknown/duplicate manifest fields, boolean integer fields, reordered parts,
  malicious path names, corrupt manifests and missing files.
- Existing output files/directories/hard links preserved, hard-linked parts
  refused, unlisted files excluded and record payloads absent from reports.
- A controlled publication I/O failure leaves a partial new split directory
  without a manifest; source bytes remain unchanged. This documents the
  nontransactional directory boundary rather than claiming rollback.
- A controlled mutation after verification changes the original part files;
  join still publishes the already captured verified bytes, avoiding an unchecked
  second read. This test deliberately violates normal immutable-input assumptions
  only at that narrow boundary.

These results do not establish real data adoption, SDK integration, hostile
filesystem race safety, production workload performance, power-loss durability
or platform behavior beyond the stated environment. Later CI results belong to
the commit they actually ran; no remote CI success is asserted by this local log.

## Closed-stdout regressions

Three real subprocess regressions start split, verify and join respectively
with `Popen(stdout=PIPE)`, immediately close the parent's read end, and wait for
termination. Before the stdio fix, all three failed: expected exit 2, observed
exit 120 with an interpreter-shutdown stdout error. Reproduction command:

```console
python -B -m unittest discover -s tests -k closed_stdout -v
```

After adding the protected report write/flush and redirecting the failed stdout
file descriptor to the null device, all three passed with exit 2 and no
`Exception ignored` shutdown diagnostic. They also assert unchanged input bytes,
a still-verifiable published split bundle, an unchanged verified bundle, and an
exactly joined output after report failure. Complete artifacts are preserved;
report failure does not promise rollback or return success.

The final fresh source-only run after this fix ran 24 cases: 23 passed and the
same Windows symlink-privilege case skipped. All six CLI checks passed. The
closed-pipe tests ran against real child processes rather than mocked stdout.
