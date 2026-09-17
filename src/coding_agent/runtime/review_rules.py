"""Pinned OCR assets; no CLI, subprocess, network or user configuration loading."""

import json
from hashlib import sha256
from importlib.resources import files

from ..core.paths import glob_matches
from ..core.review import ReviewGuidance, ReviewRuleGroup, ReviewRulesOperation

BUNDLE_SHA256 = "f1c144cb047b69fa799cb6b635e24dee44bc0a1ab35399c343450fbd3bbd6f30"
SOURCE_COMMIT = "4e59c7e815bde045158b549cdc2a7f58a32f6ba0"


def _bundle_bytes() -> bytes:
    return files("coding_agent").joinpath("data/open_code_review/rules.json").read_bytes()


def load_guidance(request: ReviewRulesOperation) -> ReviewGuidance:
    # No cache: replacement or loss of an installed asset must be detected on every call.
    if request.bundle_sha256 != BUNDLE_SHA256:
        raise ValueError("review rule bundle revision changed")
    data = _bundle_bytes()
    if sha256(data).hexdigest() != BUNDLE_SHA256:
        raise ValueError("review rule bundle is missing or modified")
    bundle = json.loads(data)
    grouped: dict[tuple[str, str], list[str]] = {}
    for path in request.paths:
        pattern, document = "default", bundle["default_document"]
        for item in bundle["patterns"]:
            if any(glob_matches(path.lower(), p.lower()) for p in item["matches"]):
                pattern, document = item["pattern"], item["document"]
                break
        grouped.setdefault((pattern, document), []).append(path)
    return ReviewGuidance(
        source_commit=SOURCE_COMMIT,
        bundle_sha256=BUNDLE_SHA256,
        limitations=(
            "Supplemental review guidance; explicit project and user rules take precedence.",
            "Rules are not review findings, executed checks or acceptance evidence.",
            "Embedded path-based rules only; no OCR file filtering or custom configuration.",
            "No content sniffing: ambiguous .m files require independent language assessment.",
        ),
        groups=tuple(
            ReviewRuleGroup(
                paths=tuple(paths),
                pattern=pattern,
                source_path="internal/config/rules/rule_docs/" + document,
                source_sha256=sha256(bundle["documents"][document].encode()).hexdigest(),
                text=bundle["documents"][document].rstrip("\n"),
            )
            for (pattern, document), paths in grouped.items()
        ),
    )
