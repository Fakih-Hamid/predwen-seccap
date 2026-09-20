import os
import re

import yaml

MANIFEST = "external_resources.yaml"

RESOURCE_TYPES = ("first_party", "third_party_tool", "third_party_content")

STATUSES = ("pending", "ready", "retired")

EMBEDDABLE_TYPES = ("first_party",)

_PLACEHOLDER = re.compile(r"^\$\{([a-z0-9_]+)\}$")

_cache = None
_cache_stamp = None


def _path(content_dir):
    return os.path.join(content_dir, MANIFEST)


def load(content_dir, force=False):
    global _cache, _cache_stamp
    path = _path(content_dir)
    stamp = (path, os.path.getmtime(path)) if os.path.exists(path) else (path, None)
    if _cache is None or force or stamp != _cache_stamp:
        data = {}
        if stamp[1] is not None:
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            for entry in raw.get("resources") or []:
                if entry.get("key"):
                    data[entry["key"]] = entry
        _cache, _cache_stamp = data, stamp
    return _cache


def clear_cache():
    global _cache, _cache_stamp
    _cache, _cache_stamp = None, None


def placeholder_key(value):
    """`${m1_github_repo}` -> `m1_github_repo`. Anything else -> None."""
    if not isinstance(value, str):
        return None
    match = _PLACEHOLDER.match(value.strip())
    return match.group(1) if match else None


def resolve(content_dir, external_url):
    if not external_url:
        return None
    key = placeholder_key(external_url)
    entry = load(content_dir).get(key) if key else None

    if entry is None:
        return {"key": key, "url": None, "status": "pending", "published": False,
                "tool_name": None, "resource_type": None, "embeddable": False,
                "unknown": key is not None}

    status = entry.get("status", "pending")
    url = entry.get("url") or None
    rtype = entry.get("resource_type")
    return {
        "key": key,
        "url": url if status == "ready" else None,
        "status": status,
        "published": bool(url) and status == "ready",
        "tool_name": entry.get("tool_name"),
        "resource_type": rtype,
        "embeddable": rtype in EMBEDDABLE_TYPES,
        "unknown": False,
    }


def validate(content_dir, artifact_dir=None):
    """Shape of the manifest itself. Called from the boot-time content check."""
    errors = []
    path = _path(content_dir)
    if not os.path.exists(path):
        return errors                      # optional; missions may declare none

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not raw.get("version"):
        errors.append(f"{MANIFEST}: no version")

    seen = set()
    for entry in raw.get("resources") or []:
        key = entry.get("key")
        if not key:
            errors.append(f"{MANIFEST}: a resource has no key")
            continue
        if key in seen:
            errors.append(f"{MANIFEST}: duplicate resource key '{key}'")
        seen.add(key)
        if not _PLACEHOLDER.match("${%s}" % key):
            errors.append(f"{MANIFEST}: '{key}' is not a valid key "
                          f"(lowercase, digits and underscores)")
        rtype = entry.get("resource_type")
        if rtype not in RESOURCE_TYPES:
            errors.append(f"{MANIFEST}: '{key}' has unknown resource_type '{rtype}'")
        status = entry.get("status", "pending")
        if status not in STATUSES:
            errors.append(f"{MANIFEST}: '{key}' has unknown status '{status}'")
        if status == "ready" and not entry.get("url"):
            errors.append(f"{MANIFEST}: '{key}' is marked ready but carries no url")
        if not entry.get("tool_name"):
            errors.append(f"{MANIFEST}: '{key}' names no tool")
        note = entry.get("participant_note")
        if note is not None:
            if not isinstance(note, dict):
                errors.append(f"{MANIFEST}: '{key}' has a participant_note that "
                              f"is not a {{ja, en}} pair")
            else:
                for half in ("ja", "en"):
                    value = note.get(half)
                    if not (isinstance(value, str) and value.strip()):
                        errors.append(f"{MANIFEST}: '{key}' participant_note has "
                                      f"no '{half}' half")

        snapshot = entry.get("fallback_snapshot")
        if not snapshot:
            errors.append(f"{MANIFEST}: '{key}' has no fallback_snapshot — every "
                          f"external resource needs a local copy to grade against")
        elif artifact_dir:
            full = os.path.join(artifact_dir, snapshot.replace("/", os.sep))
            if not os.path.exists(full):
                errors.append(f"{MANIFEST}: '{key}' falls back to a missing file "
                              f"'{snapshot}'")
    return errors


def validate_references(content_dir, missions):
    errors = []
    known = set(load(content_dir))
    for m in missions:
        slug = m.get("slug", "?")
        for a in m.get("artifacts") or []:
            for field, raw in (("external_url", a.get("external_url")),
                               ("how_to.tool_link",
                                (a.get("how_to") or {}).get("tool_link"))):
                if not raw:
                    continue
                key = placeholder_key(raw)
                if key is None:
                    errors.append(
                        f"{slug}: artifact '{a.get('id')}' has a literal "
                        f"{field}. Use a ${{placeholder}} declared in "
                        f"{MANIFEST} so the URL lives in one place and can be "
                        f"marked pending.")
                elif key not in known:
                    errors.append(f"{slug}: artifact '{a.get('id')}' {field} "
                                  f"references unknown external resource "
                                  f"'{key}'")
    return errors


def summary(content_dir):
    """Counts for the facilitator console and the morning check."""
    entries = load(content_dir).values()
    out = {s: 0 for s in STATUSES}
    for entry in entries:
        status = entry.get("status", "pending")
        if status in out:
            out[status] += 1
    out["total"] = len(list(entries))
    return out
