"""Repeatable CLI examples; intentionally propagate expected 1/2 error codes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

SCRIPT = Path(__file__).resolve().parents[1] / "jsonl_byte_shards.py"


def invoke(*args, expected=0):
    result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                            capture_output=True, text=True)
    if result.returncode != expected:
        raise RuntimeError("unexpected demo exit code")
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=["roundtrip", "tamper", "occupied"])
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="jsonl-shards-demo-") as temporary:
        base = Path(temporary)
        source = base / "source.jsonl"
        data = '{"example":"雪"}\r\n42\n[true,null]'.encode("utf-8")
        source.write_bytes(data)
        bundle = base / "bundle"
        invoke("split", source, "--out-dir", bundle, "--part-bytes", 24)
        manifest = bundle / "manifest.json"
        if args.case == "tamper":
            (bundle / "part-00001.jsonl").write_bytes(b'0\n')
            return invoke("verify", manifest, expected=1)
        output = base / "joined.jsonl"
        if args.case == "occupied":
            output.write_bytes(b"preserved synthetic output")
            code = invoke("join", manifest, "--out", output, expected=2)
            if output.read_bytes() != b"preserved synthetic output":
                raise RuntimeError("existing output was modified")
            return code
        invoke("verify", manifest)
        invoke("join", manifest, "--out", output)
        if output.read_bytes() != data:
            raise RuntimeError("roundtrip byte mismatch")
        print(json.dumps({"status": "ok", "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest()}))
        return 0


if __name__ == "__main__":
    sys.exit(main())
