"""Guard against silent YAML truncation: ` #` in a plain scalar starts a comment.

`notes: ... issue #9.` is stored as `... issue`, so issue references in plain values must be quoted.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
# key: <plain scalar> ... #<digit>  (an issue reference that YAML would drop as a comment)
TRUNCATED = re.compile(r"^\s*-?\s*[A-Za-z_][\w.]*: [^\s\"'\[{#|>].* #\d")


def test_no_issue_reference_lost_to_a_yaml_comment() -> None:
    hits = [
        f"{path.relative_to(ROOT)}:{n}"
        for path in sorted((ROOT / "config").rglob("*.yaml"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if TRUNCATED.match(line)
    ]
    assert hits == [], f"quote these values, ' #' starts a YAML comment: {hits}"


def test_quoted_notes_keep_issue_references() -> None:
    data = yaml.safe_load((ROOT / "config" / "parameters.yaml").read_text(encoding="utf-8"))
    text = repr(data)
    for ref in ("issue #9).", "issue #15.", "issue #11).", "issue #13 consumes", "issue #23"):
        assert ref in text, ref
