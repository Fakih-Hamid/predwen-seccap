import hashlib
import json
import os

from . import missions as content

KINDS = ("json", "text", "gitlog", "table", "web", "sandbox", "image", "headers")

MAX_BYTES = 512 * 1024

DOWNLOAD_MAX_BYTES = 8 * 1024 * 1024


def resolve(artifact_dir, rel_path):
    """Absolute path, or None if it escapes the artifact directory."""
    if not rel_path:
        return None
    root = os.path.realpath(artifact_dir)
    full = os.path.realpath(os.path.join(root, rel_path.replace("/", os.sep)))
    if full != root and not full.startswith(root + os.sep):
        return None
    return full if os.path.isfile(full) else None


def read_bytes(artifact_dir, rel_path):
    full = resolve(artifact_dir, rel_path)
    if full is None:
        return None
    with open(full, "rb") as f:
        return f.read(MAX_BYTES)


def sha256_of(artifact_dir, rel_path):
    raw = read_bytes(artifact_dir, rel_path)
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def snapshot_path(entry):
    return entry.get("path") or entry.get("fallback_snapshot")


def artifact_brief(entry, lang, content_dir=None):
    kind = entry.get("type", "text")
    return {
        "id": entry["id"],
        "type": kind if kind in KINDS else "text",
        "title": content.tx(entry.get("title"), lang),
        "note": content.tx(entry.get("note"), lang),
        "icon": entry.get("icon", "▤"),
        "tool": entry.get("tool"),
        "filename": os.path.basename(snapshot_path(entry) or ""),
        "how_to": content.render_how_to(entry, lang, content_dir),
        "body": None,
        "data": None,
        "missing": False,
        "withheld": True,
    }


def artifact_payload(artifact_dir, entry, lang, content_dir=None):
    out = artifact_brief(entry, lang, content_dir)
    out["withheld"] = False
    rel = snapshot_path(entry)
    raw = read_bytes(artifact_dir, rel) if rel else None
    if rel and raw is None:
        out["missing"] = True
        return out
    if raw is None:
        return out

    kind = out["type"]
    if kind in ("json", "sandbox", "table", "headers", "web"):
        try:
            out["data"] = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            out["body"] = raw.decode("utf-8", "replace")
    elif kind == "image":
        out["binary"] = True
    else:
        out["body"] = raw.decode("utf-8", "replace")
    return out
