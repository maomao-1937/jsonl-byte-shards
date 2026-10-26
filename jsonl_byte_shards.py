#!/usr/bin/env python3
"""Bounded, byte-preserving JSONL sharding. Python 3.11+, standard library."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


MAX_TOTAL = 64 * 1024 * 1024
MAX_LINE = 1024 * 1024
MAX_RECORDS = 100000
MAX_PARTS = 1000
MAX_MANIFEST = 1024 * 1024
MAX_PART_BYTES = 16 * 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ShardError(Exception):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def digest(data):
    return hashlib.sha256(data).hexdigest()


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError("nonfinite constant")


def parse_json(data, numeric_lexemes=False):
    options = {"object_pairs_hook": no_duplicates, "parse_constant": reject_constant}
    if numeric_lexemes:
        options.update(parse_int=str, parse_float=str)
    try:
        # Explicit UTF8 decoding refuses a BOM instead of JSON's bytes autodetection.
        return json.loads(data.decode("utf-8"), **options)
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise ShardError("invalid or unsupported strict UTF8 JSON") from None


def check_nodes(path):
    """Reject symlinks and Windows reparse points in the observed path chain."""
    absolute = Path(os.path.abspath(path))
    for node in (*reversed(absolute.parents), absolute):
        try:
            info = node.lstat()
        except FileNotFoundError:
            raise ShardError("required path does not exist") from None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ShardError("links and reparse paths are unsupported")
    return absolute


def require_new_output(path):
    path = Path(os.path.abspath(path))
    check_nodes(path.parent)
    if os.path.lexists(path):
        raise ShardError("output path already exists")
    return path


def read_bounded(path, limit):
    path = check_nodes(path)
    observed = path.stat()
    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
        raise ShardError("input must be a regular file with one hard link")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ShardError("input must be a regular file with one hard link")
        if before.st_size > limit:
            raise ShardError("file exceeds hard byte limit")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if len(data) > limit:
            raise ShardError("file exceeds hard byte limit")
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ShardError("input changed while being read")
    return data


def records(data, content_code=2):
    """Yield complete raw records; a line budget includes its CR/LF bytes."""
    stream = io.BytesIO(data)
    count = 0
    while True:
        raw = stream.readline(MAX_LINE + 1)
        if not raw:
            break
        count += 1
        if len(raw) > MAX_LINE or count > MAX_RECORDS:
            raise ShardError("record byte/count limit exceeded")
        body = raw[:-1] if raw.endswith(b"\n") else raw
        if raw.endswith(b"\r\n"):
            body = body[:-1]
        if not body.strip():
            raise ShardError("blank JSONL record", content_code)
        try:
            parse_json(body, numeric_lexemes=True)
        except ShardError:
            raise ShardError("invalid strict JSONL record", content_code) from None
        yield raw


def integer(value, lower, upper):
    return type(value) is int and lower <= value <= upper


def exact_keys(value, keys):
    return type(value) is dict and set(value) == set(keys)


def valid_hash(value):
    return type(value) is str and SHA256.fullmatch(value) is not None


def validate_schema(value):
    if not exact_keys(value, ("format_version", "source", "part_bytes", "parts")):
        raise ShardError("unsupported manifest schema")
    if type(value["format_version"]) is not int or value["format_version"] != 1:
        raise ShardError("unsupported manifest version")
    if not integer(value["part_bytes"], 1, MAX_PART_BYTES):
        raise ShardError("invalid part byte limit")
    source = value["source"]
    if not exact_keys(source, ("bytes", "records", "sha256")):
        raise ShardError("invalid source schema")
    if not integer(source["bytes"], 1, MAX_TOTAL) or not integer(source["records"], 1, MAX_RECORDS) or not valid_hash(source["sha256"]):
        raise ShardError("invalid source fields or hard limits")
    parts = value["parts"]
    if type(parts) is not list or not 1 <= len(parts) <= MAX_PARTS:
        raise ShardError("invalid part list or hard limit")
    for index, part in enumerate(parts, 1):
        if not exact_keys(part, ("filename", "bytes", "first_record", "last_record", "sha256")):
            raise ShardError("invalid part schema")
        if part["filename"] != f"part-{index:05d}.jsonl":
            raise ShardError("part filename or order is unsupported")
        if not integer(part["bytes"], 1, MAX_PART_BYTES) or not valid_hash(part["sha256"]):
            raise ShardError("invalid part fields or hard limits")
        if not integer(part["first_record"], 1, MAX_RECORDS) or not integer(part["last_record"], part["first_record"], MAX_RECORDS):
            raise ShardError("invalid part record range")
    if sum(part["bytes"] for part in parts) > MAX_TOTAL:
        raise ShardError("declared parts exceed hard byte limit")
    return value


def verify_bundle(manifest_path):
    """Return manifest and captured validated part bytes, never paths to reread."""
    manifest_path = Path(manifest_path)
    manifest = validate_schema(parse_json(read_bounded(manifest_path, MAX_MANIFEST)))
    captured = []
    combined = hashlib.sha256()
    total_bytes = 0
    total_records = 0
    previous_bytes = None
    identities = set()
    for index, part in enumerate(manifest["parts"]):
        path = manifest_path.parent / part["filename"]
        # Reject aliases, including hard links, before parsing any contents.
        info = check_nodes(path).stat()
        identity = (info.st_dev, info.st_ino)
        if identity in identities:
            raise ShardError("duplicate part file identity")
        identities.add(identity)
        data = read_bounded(path, MAX_PART_BYTES)
        total_bytes += len(data)
        if total_bytes > MAX_TOTAL:
            raise ShardError("actual parts exceed hard byte limit")
        if len(data) > manifest["part_bytes"]:
            raise ShardError("part exceeds manifest byte budget", 1)
        if len(data) != part["bytes"] or digest(data) != part["sha256"]:
            raise ShardError("part byte count or SHA256 mismatch", 1)
        if index < len(manifest["parts"]) - 1 and not data.endswith(b"\n"):
            raise ShardError("nonfinal part lacks LF", 1)
        part_count = 0
        first_size = None
        for raw in records(data, content_code=1):
            if first_size is None:
                first_size = len(raw)
            part_count += 1
        if not part_count:
            raise ShardError("empty part", 1)
        if previous_bytes is not None and previous_bytes + first_size <= manifest["part_bytes"]:
            raise ShardError("parts do not use greedy record packing", 1)
        first = total_records + 1
        total_records += part_count
        if total_records > MAX_RECORDS:
            raise ShardError("actual records exceed hard limit")
        if part["first_record"] != first or part["last_record"] != total_records:
            raise ShardError("part record range mismatch", 1)
        previous_bytes = len(data)
        combined.update(data)
        captured.append(data)
    source = manifest["source"]
    if (total_bytes, total_records, combined.hexdigest()) != (source["bytes"], source["records"], source["sha256"]):
        raise ShardError("combined source counts or SHA256 mismatch", 1)
    return manifest, captured


def write_new(path, data):
    with Path(path).open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def split_bundle(input_path, out_dir, part_bytes):
    if not integer(part_bytes, 1, MAX_PART_BYTES):
        raise ShardError("part byte budget outside hard limits")
    out_dir = require_new_output(out_dir)
    data = read_bounded(input_path, MAX_TOTAL)
    packed = []
    current = bytearray()
    first_record = 1
    count = 0
    for raw in records(data):
        if len(raw) > part_bytes:
            raise ShardError("record exceeds requested part byte budget")
        if current and len(current) + len(raw) > part_bytes:
            packed.append((bytes(current), first_record, count))
            if len(packed) >= MAX_PARTS:
                raise ShardError("part count exceeds hard limit")
            current.clear()
            first_record = count + 1
        current.extend(raw)
        count += 1
    if not count:
        raise ShardError("empty input is unsupported")
    packed.append((bytes(current), first_record, count))
    manifest = {"format_version": 1,
                "source": {"bytes": len(data), "records": count, "sha256": digest(data)},
                "part_bytes": part_bytes,
                "parts": [{"filename": f"part-{index:05d}.jsonl", "bytes": len(raw),
                           "first_record": first, "last_record": last, "sha256": digest(raw)}
                          for index, (raw, first, last) in enumerate(packed, 1)]}
    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
    with tempfile.TemporaryDirectory(prefix=".jsonl-shards-", dir=out_dir.parent) as temporary:
        stage = Path(temporary)
        for part, (raw, _first, _last) in zip(manifest["parts"], packed):
            write_new(stage / part["filename"], raw)
        write_new(stage / "manifest.json", manifest_bytes)
        verified, captured = verify_bundle(stage / "manifest.json")
        # mkdir is exclusive on Windows and POSIX; never rename onto an existing dir.
        out_dir.mkdir()
        # A later I/O failure may leave this new directory incomplete. Manifest last.
        for part, raw in zip(verified["parts"], captured):
            write_new(out_dir / part["filename"], raw)
        write_new(out_dir / "manifest.json", manifest_bytes)
    return verified


def join_bundle(manifest_path, output_path):
    output_path = require_new_output(output_path)
    manifest, captured = verify_bundle(manifest_path)
    descriptor, temporary = tempfile.mkstemp(prefix=".jsonl-join-", dir=output_path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            for data in captured:
                stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        staged = read_bounded(temporary, MAX_TOTAL)
        if len(staged) != manifest["source"]["bytes"] or digest(staged) != manifest["source"]["sha256"]:
            raise ShardError("staged join failed validation", 1)
        # Hard-link publication is exclusive: an occupied destination always fails.
        # The staging file is on the same filesystem; unsupported hard links =>2.
        os.link(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def summary(action, manifest):
    return {"status": "ok", "action": action, "parts": len(manifest["parts"]),
            **manifest["source"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    split = commands.add_parser("split", help="write a new verified bundle")
    split.add_argument("input", type=Path)
    split.add_argument("--out-dir", type=Path, required=True)
    split.add_argument("--part-bytes", type=int, required=True)
    verify = commands.add_parser("verify", help="verify strict manifest and actual parts")
    verify.add_argument("manifest", type=Path)
    join = commands.add_parser("join", help="verify and join into a new file")
    join.add_argument("manifest", type=Path)
    join.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "split":
            manifest = split_bundle(args.input, args.out_dir, args.part_bytes)
        elif args.action == "verify":
            manifest, _captured = verify_bundle(args.manifest)
        else:
            manifest = join_bundle(args.manifest, args.out)
        try:
            print(json.dumps(summary(args.action, manifest), sort_keys=True), flush=True)
        except OSError:
            # Reporting can fail after publication. Preserve the artifacts and
            # exit 2; discard pending stdout to prevent shutdown exit 120.
            with open(os.devnull, 'w') as sink:
                os.dup2(sink.fileno(), sys.stdout.fileno())
            raise
        return 0
    except ShardError as exc:
        print(json.dumps({"status": "error", "code": exc.code, "reason": str(exc)}), file=sys.stderr)
        return exc.code
    except OSError:
        # Paths and platform exception text may contain user-controlled payload.
        print(json.dumps({"status": "error", "code": 2, "reason": "filesystem/report I/O or setup failure; output may already exist and a new split directory may be incomplete"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
