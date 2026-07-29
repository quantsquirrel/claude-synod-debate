#!/usr/bin/env python3
"""Citation Verifier — mechanical file:line citation checking (v3.9, Phase 4.5).

Background
----------
The v3.5 evidence gate only COUNTED citation-shaped strings in the synthesis
markdown — a fabricated `utils.py:9999` scored the same as a real citation, so
"evidence-based" could be earned with invented evidence. Grounded-debate
research (Tool-MAD arXiv:2601.04742; MAST arXiv:2503.13657 — 21% of multi-agent
failures trace to weak verification) says reliability comes from verifying
evidence, not from more debate rounds. This tool closes the gap with STRICTLY
MECHANICAL checks:

  1. the cited file exists under TARGET_PATH
  2. the cited line number is within the file's length

Semantic relatedness of the line content to the claim is NOT checked — that
would require an LLM judge again. An optional keyword-overlap signal is
reported for observability but never affects the verdict.

Per-citation verdicts
---------------------
  verified   — file exists, line (range) within file length
  bad_line   — file exists, cited line exceeds file length  (fabrication signal)
  not_found  — no such file under TARGET_PATH               (fabrication signal)
  ambiguous  — bare filename matches >1 file under TARGET_PATH; not scored
  outside    — absolute path outside TARGET_PATH; not scored

`bad_line` + `not_found` are surfaced as fabricated-citation findings.
Coverage math counts only decidable citations (verified/bad_line/not_found).

Fail-safe: any unexpected error yields a JSON report with status="error" and
exit code 0 — the gate informs, it never blocks the pipeline.

Usage
-----
  citation_verifier.py --target <repo> --file <response.md>
  citation_verifier.py --target <repo> --dir <round-1-solver>   # per-model *.md
"""

from __future__ import annotations

import argparse
import glob as _glob
import json
import os
import re
import sys
from typing import Any

# Mirrors the Phase 4.5 citation pattern (synod-phase4-5-evidence-gate.md),
# extended with common source extensions. Group 1 = path, 3 = start, 4 = end.
CITATION_RE = re.compile(
    r"(\S+?\.(py|ts|tsx|js|jsx|go|rs|java|rb|md|json|toml|yaml|yml|sh|c|h|cpp|hpp))"
    r":(\d+)(?:-(\d+))?"
)

_STOPCHARS = "\"'`()[]{}<>,;"


def _clean_path(raw: str) -> str:
    return raw.strip(_STOPCHARS)


def _file_line_count(path: str) -> int:
    with open(path, "rb") as f:
        return sum(1 for _ in f)


def _resolve(cited: str, target: str) -> tuple[str, str | None]:
    """Resolve a cited path against TARGET_PATH.

    Returns (resolution, abs_path):
      ("exact", path)     — relative or absolute path exists under target
      ("unique", path)    — bare basename matched exactly one file under target
      ("ambiguous", None) — basename matched several files
      ("outside", None)   — absolute path not under target
      ("missing", None)   — nothing matched
    """
    target_abs = os.path.abspath(target)

    if os.path.isabs(cited):
        cited_abs = os.path.abspath(cited)
        if not (cited_abs + os.sep).startswith(target_abs + os.sep) and cited_abs != target_abs:
            return ("outside", None)
        return ("exact", cited_abs) if os.path.isfile(cited_abs) else ("missing", None)

    joined = os.path.abspath(os.path.join(target_abs, cited))
    if os.path.isfile(joined):
        return ("exact", joined)

    # Bare basename (or unmatched relative path): search under target.
    basename = os.path.basename(cited)
    matches = [
        m
        for m in _glob.glob(os.path.join(target_abs, "**", basename), recursive=True)
        if os.path.isfile(m)
    ]
    # A relative path like tools/x.py must also match its directory suffix.
    if os.sep in cited:
        matches = [m for m in matches if m.endswith(os.sep + cited)]
    if len(matches) == 1:
        return ("unique", matches[0])
    if len(matches) > 1:
        return ("ambiguous", None)
    return ("missing", None)


def _keyword_overlap(claim_line: str, file_line: str) -> float:
    """Observability-only: crude token overlap between claim text and cited line."""
    tok = lambda s: {t for t in re.split(r"\W+", s.lower()) if len(t) > 2}
    a, b = tok(claim_line), tok(file_line)
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 3)


def verify_text(text: str, target: str) -> dict[str, Any]:
    """Verify every file:line citation in `text` against `target`."""
    citations = []
    seen = set()
    for line in text.splitlines():
        for m in CITATION_RE.finditer(line):
            raw_path = _clean_path(m.group(1))
            start = int(m.group(3))
            end = int(m.group(4)) if m.group(4) else start
            key = (raw_path, start, end)
            if key in seen:
                continue
            seen.add(key)

            resolution, abs_path = _resolve(raw_path, target)
            entry: dict[str, Any] = {
                "citation": f"{raw_path}:{m.group(3)}" + (f"-{m.group(4)}" if m.group(4) else ""),
                "path": raw_path,
                "line_start": start,
                "line_end": end,
            }
            if resolution in ("exact", "unique"):
                try:
                    n_lines = _file_line_count(abs_path)
                except OSError:
                    entry["verdict"] = "not_found"
                    citations.append(entry)
                    continue
                if start <= n_lines and end <= n_lines and start <= end:
                    entry["verdict"] = "verified"
                    try:
                        with open(abs_path, errors="replace") as f:
                            cited_line = f.readlines()[start - 1]
                        entry["keyword_overlap"] = _keyword_overlap(line, cited_line)
                    except (OSError, IndexError):
                        pass
                else:
                    entry["verdict"] = "bad_line"
                    entry["file_lines"] = n_lines
            elif resolution == "ambiguous":
                entry["verdict"] = "ambiguous"
            elif resolution == "outside":
                entry["verdict"] = "outside"
            else:
                entry["verdict"] = "not_found"
            citations.append(entry)

    verdicts = [c["verdict"] for c in citations]
    verified = verdicts.count("verified")
    fabricated = [
        c for c in citations if c["verdict"] in ("bad_line", "not_found")
    ]
    decidable = verified + len(fabricated)
    return {
        "status": "ok",
        "total_citations": len(citations),
        "verified": verified,
        "fabricated": len(fabricated),
        "undecidable": len(citations) - decidable,
        "verified_rate": round(verified / decidable, 3) if decidable else None,
        "fabricated_citations": [c["citation"] for c in fabricated],
        "citations": citations,
    }


def verify_file(path: str, target: str) -> dict[str, Any]:
    try:
        with open(path, errors="replace") as f:
            report = verify_text(f.read(), target)
        report["source"] = os.path.basename(path)
        return report
    except OSError as exc:
        return {"status": "error", "source": os.path.basename(path), "error": str(exc)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mechanically verify file:line citations against a target repo."
    )
    parser.add_argument("--target", required=True, help="TARGET_PATH the citations refer to.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="Single markdown/text file to verify.")
    group.add_argument("--dir", help="Directory of *.md response files (per-model reports).")

    args = parser.parse_args()

    try:
        if not os.path.isdir(args.target):
            print(json.dumps({"status": "error", "error": f"target not a directory: {args.target}"}))
            sys.exit(0)
        if args.file:
            result: Any = verify_file(args.file, args.target)
        else:
            reports = [
                verify_file(p, args.target)
                for p in sorted(_glob.glob(os.path.join(args.dir, "*.md")))
            ]
            result = {"status": "ok", "reports": reports}
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception as exc:  # fail-safe: report, never block the pipeline
        print(json.dumps({"status": "error", "error": str(exc)}))
    sys.exit(0)


if __name__ == "__main__":
    main()
