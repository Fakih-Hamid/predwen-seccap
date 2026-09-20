import glob
import hashlib
import os
import re

import yaml

from . import external

CATEGORY_KEYS = ("correctness", "evidence", "reasoning", "completeness", "speed")

HINT_LEVELS = ("orientation", "pivot", "tool", "method", "recovery")
HINT_COSTS = {level: 0 for level in HINT_LEVELS}

HINT_UNLOCK_AFTER = {
    "orientation": 0,
    "pivot": 0,
    "tool": 240,
    "method": 480,
    "recovery": 720,
}


PIVOT_HINT_PREFIX = "pv:"


def pivot_hint_id(artifact_id, level):
    return f"{PIVOT_HINT_PREFIX}{artifact_id}:{level}"


def parse_pivot_hint_id(hint_id):
    """(artifact_id, level) for a pivot rung, or (None, None)."""
    if not str(hint_id).startswith(PIVOT_HINT_PREFIX):
        return None, None
    rest = str(hint_id)[len(PIVOT_HINT_PREFIX):]
    artifact_id, _, level = rest.rpartition(":")
    return (artifact_id or None), (level or None)


def hint_unlock_after(hint):
    """Seconds into the mission before this hint opens on its own."""
    if hint.get("unlock_after") is not None:
        return max(0, int(hint["unlock_after"]))
    return HINT_UNLOCK_AFTER.get(hint.get("level"), 0)

QUESTION_TYPES = (
    "short_text",      # one string, compared case-insensitively after normalisation
    "choice",          # one option id
    "multi_choice",    # a set of option ids
    "order",           # an ordered list of item ids
    "graph",           # a set of "src|relation|dst" edges, built from controlled lists
    "confidence",      # low | medium | high, with an authored defensible range
    "free_text",       # facilitator-graded; never auto-scored
)

AUTO_TYPES = ("short_text", "choice", "multi_choice", "order", "graph", "confidence")

_cache = None
_cache_stamp = None


def _dirs(app_config):
    return app_config["CONTENT_DIR"], app_config["ARTIFACT_DIR"]


def _paths(content_dir):
    return sorted(glob.glob(os.path.join(content_dir, "missions", "*.yaml")))


def _stamp(paths):
    """(path, mtime) fingerprint — invalidates the cache when a file changes."""
    return tuple((p, os.path.getmtime(p)) for p in paths)


def load_missions(content_dir, force=False):
    global _cache, _cache_stamp
    paths = _paths(content_dir)
    stamp = _stamp(paths)
    if _cache is None or force or stamp != _cache_stamp:
        loaded = {}
        for path in paths:
            with open(path, encoding="utf-8") as f:
                m = yaml.safe_load(f)
            m["_path"] = os.path.relpath(path, content_dir)
            loaded[m["slug"]] = m
        _cache, _cache_stamp = loaded, stamp
    return _cache


def clear_cache():
    """Tests build several content trees in one process."""
    global _cache, _cache_stamp
    _cache, _cache_stamp = None, None


def get_mission(content_dir, slug):
    return load_missions(content_dir).get(slug)


def ordered_missions(content_dir):
    ms = [m for m in load_missions(content_dir).values() if m.get("kind", "mission") == "mission"]
    return sorted(ms, key=lambda m: m.get("order", 0))


def final_definition(content_dir):
    for m in load_missions(content_dir).values():
        if m.get("kind") == "final":
            return m
    return None


def tx(pair, lang):
    if pair is None:
        return None
    if isinstance(pair, str):
        return pair
    if not isinstance(pair, dict):
        return pair
    return pair.get("en" if lang == "en" else "ja") or pair.get("ja") or pair.get("en")


def tx_list(items, lang):
    if items is None:
        return []
    if isinstance(items, dict):
        return [tx(items, lang)]
    if isinstance(items, str):
        return [items]
    return [tx(i, lang) for i in items]


_NON_TEXT_KEYS = ("_path", "validator", "accept", "correct", "slug", "id", "key",
                  "type", "kind", "path", "points", "weight", "cost", "level",
                  "order", "version", "budget", "duration_seconds", "artifact",
                  "artifacts_required", "evidence_points", "defensible",
                  "category", "ioc", "technique", "seconds",
                  "external_url", "tool_name", "resource_type",
                  "fallback_snapshot", "availability_check")


def validate_language_coverage(node, slug, path=""):
    errors = []
    if isinstance(node, dict):
        if {"ja", "en"} & set(node):
            for half in ("ja", "en"):
                value = node.get(half)
                if value is None:
                    errors.append(f"{slug}: '{path or 'root'}' has no '{half}' half")
                elif isinstance(value, str) and not value.strip():
                    errors.append(f"{slug}: '{path or 'root'}' has an empty '{half}' half")
        for key, value in node.items():
            if key in _NON_TEXT_KEYS:
                continue
            errors += validate_language_coverage(value, slug, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            errors += validate_language_coverage(value, slug, f"{path}[{i}]")
    return errors


def validate_budgets(m):
    """Authored points must add up to the authored budget, category by category."""
    errors = []
    slug = m.get("slug", "?")
    budget = m.get("budget") or {}
    for key in budget:
        if key not in CATEGORY_KEYS:
            errors.append(f"{slug}: unknown budget category '{key}'")

    totals = {k: 0.0 for k in CATEGORY_KEYS}
    for q in m.get("questions") or []:
        cat = q.get("category", "correctness")
        if cat not in CATEGORY_KEYS:
            errors.append(f"{slug}: question '{q.get('key')}' has unknown category '{cat}'")
            continue
        totals[cat] += float(q.get("points", 0))
        totals["evidence"] += float(q.get("evidence_points", 0))
    for r in m.get("rubric") or []:
        cat = r.get("category", "reasoning")
        if cat not in CATEGORY_KEYS:
            errors.append(f"{slug}: rubric '{r.get('key')}' has unknown category '{cat}'")
            continue
        totals[cat] += float(r.get("points", 0))

    for key in CATEGORY_KEYS:
        want = float(budget.get(key, 0))
        got = round(totals[key], 3)
        if key == "speed":
            continue
        if abs(got - want) > 0.001:
            errors.append(f"{slug}: category '{key}' budget is {want} but items add up to {got}")

    declared = float(m.get("max_points", 0))
    total = sum(float(budget.get(k, 0)) for k in CATEGORY_KEYS)
    if declared and abs(declared - total) > 0.001:
        errors.append(f"{slug}: max_points {declared} does not match the budget total {total}")
    return errors


def _validate_ladder(hints, slug, where, seen_ids, require_full=False):
    errors = []
    levels = []
    for h in hints:
        hid = h.get("id")
        if not hid:
            errors.append(f"{slug}: a hint in {where} has no id")
            continue
        if hid in seen_ids:
            errors.append(f"{slug}: duplicate hint id '{hid}'")
        seen_ids.add(hid)

        level = h.get("level")
        if level not in HINT_COSTS:
            errors.append(f"{slug}: hint '{hid}' has unknown level '{level}'. "
                          f"The ladder is {' → '.join(HINT_LEVELS)}")
            continue
        levels.append(level)
        if int(h.get("cost", -1)) != HINT_COSTS[level]:
            errors.append(f"{slug}: hint '{hid}' costs {h.get('cost')}. Hints are free; "
                          f"the ladder carries the escalation, not a price")
        if not h.get("text"):
            errors.append(f"{slug}: hint '{hid}' has no text")

    rank = {level: i for i, level in enumerate(HINT_LEVELS)}
    ordered = [rank[level] for level in levels]
    if ordered != sorted(ordered):
        errors.append(f"{slug}: {where} is out of order — rungs must be authored "
                      f"{' → '.join(HINT_LEVELS)}")
    unlocks = [hint_unlock_after(h) for h in hints if h.get("level") in rank]
    if unlocks != sorted(unlocks):
        errors.append(f"{slug}: {where} unlocks out of order — a later rung may not "
                      f"open before an earlier one")

    if require_full:
        missing = [level for level in HINT_LEVELS if level not in levels]
        if missing:
            errors.append(f"{slug}: {where} is missing the {', '.join(missing)} rung(s). "
                          f"Every mission carries the full ladder, recovery included")
    return errors


def validate_structure(m, artifact_dir):
    """Ids, types, artifact files, hint costs and validator shapes."""
    errors = []
    slug = m.get("slug", "?")
    if not slug or slug == "?":
        errors.append("a mission file has no slug")
    if not m.get("version"):
        errors.append(f"{slug}: no version (a session records which one it ran)")

    if m.get("kind", "mission") == "mission" and not m.get("first_step"):
        errors.append(f"{slug}: no first_step — every mission must name one concrete "
                      f"opening move")

    loop = m.get("loop") or []
    timed = [step for step in loop if isinstance(step, dict) and step.get("seconds")]
    if timed and len(timed) != len(loop):
        errors.append(f"{slug}: some loop steps carry 'seconds' and some do not — "
                      f"a partial rhythm cannot be shown against the clock")
    if timed and len(timed) == len(loop):
        total = sum(int(step["seconds"]) for step in timed)
        want = int(m.get("duration_seconds", 1800))
        if total != want:
            errors.append(f"{slug}: loop steps add up to {total}s but the mission runs "
                          f"for {want}s")

    artifact_ids = set()
    for a in m.get("artifacts") or []:
        aid = a.get("id")
        if not aid:
            errors.append(f"{slug}: an artifact has no id")
            continue
        if aid in artifact_ids:
            errors.append(f"{slug}: duplicate artifact id '{aid}'")
        artifact_ids.add(aid)
        rel = a.get("path")
        if rel:
            full = os.path.join(artifact_dir, rel.replace("/", os.sep))
            if not os.path.exists(full):
                errors.append(f"{slug}: artifact '{aid}' points at a missing file '{rel}'")

        if a.get("external_url"):
            snapshot = a.get("fallback_snapshot") or rel
            if not snapshot:
                errors.append(f"{slug}: artifact '{aid}' is external but has no "
                              f"fallback_snapshot and no local path")
            else:
                full = os.path.join(artifact_dir, snapshot.replace("/", os.sep))
                if not os.path.exists(full):
                    errors.append(f"{slug}: artifact '{aid}' falls back to a missing "
                                  f"file '{snapshot}'")
            if not a.get("tool_name"):
                errors.append(f"{slug}: artifact '{aid}' is external and names no tool")
            rtype = a.get("resource_type")
            if rtype not in external.RESOURCE_TYPES:
                errors.append(f"{slug}: artifact '{aid}' has unknown resource_type "
                              f"'{rtype}'")
            if not a.get("expected_pivot"):
                errors.append(f"{slug}: artifact '{aid}' is external and declares no "
                              f"expected_pivot — a team sent outside must know what "
                              f"it is going out for")
        else:
            for field in ("tool_name", "resource_type", "fallback_snapshot",
                          "availability_check", "expected_pivot", "safety_notice"):
                if a.get(field):
                    errors.append(f"{slug}: artifact '{aid}' sets '{field}' but has no "
                                  f"external_url, so it is never launched")

    from .state import SOURCES_AT_START

    opening = (tx(m.get("first_step"), "en") or "").lower()
    if opening:
        late = [a for a in (m.get("artifacts") or [])[SOURCES_AT_START:]
                if (tx(a.get("title"), "en") or "").lower() in opening
                or (a.get("path") and os.path.basename(a["path"]).lower() in opening)]
        for a in late:
            errors.append(f"{slug}: `first_step` points at source '{a['id']}', "
                          f"which is not one of the first {SOURCES_AT_START} and "
                          f"is therefore not on the page when the mission opens")

    seen_q = set()
    for q in m.get("questions") or []:
        key = q.get("key")
        if not key:
            errors.append(f"{slug}: a question has no key")
            continue
        if key in seen_q:
            errors.append(f"{slug}: duplicate question key '{key}'")
        seen_q.add(key)
        qtype = q.get("type")
        if qtype not in QUESTION_TYPES:
            errors.append(f"{slug}: question '{key}' has unknown type '{qtype}'")
        marked = any(o.get("correct") for o in q.get("options") or [])
        if qtype in AUTO_TYPES and not q.get("validator") and not marked:
            errors.append(f"{slug}: question '{key}' is auto-scored but carries neither a "
                          f"validator nor an option marked correct")
        if qtype == "free_text" and q.get("validator"):
            errors.append(f"{slug}: question '{key}' is free text and must not carry a validator")
        if qtype in ("choice", "multi_choice") and not q.get("options"):
            errors.append(f"{slug}: question '{key}' is a choice question with no options")
        for opt in q.get("options") or []:
            if not opt.get("id"):
                errors.append(f"{slug}: an option of '{key}' has no id")
        for aid in q.get("accepted_evidence") or []:
            if aid not in artifact_ids:
                errors.append(f"{slug}: question '{key}' accepts evidence from unknown artifact '{aid}'")
        if q.get("evidence_points") and not q.get("accepted_evidence"):
            errors.append(f"{slug}: question '{key}' awards evidence points but accepts no artifact")

    seen_h = set()
    errors += _validate_ladder(m.get("hints") or [], slug, "mission ladder", seen_h,
                               require_full=m.get("kind", "mission") == "mission")
    for a in m.get("artifacts") or []:
        aid = a.get("id")
        ladder = a.get("hints") or []

        how = a.get("how_to")
        if how is not None:
            if not (how.get("steps") or how.get("commands")):
                errors.append(f"{slug}: artifact '{aid}' has a how_to with neither "
                              f"steps nor commands, so it renders an empty panel")
            for c in how.get("commands") or []:
                if not c.get("cmd"):
                    errors.append(f"{slug}: artifact '{aid}' has a how_to command "
                                  f"with no `cmd`")
                label = c.get("os")
                if not label:
                    errors.append(f"{slug}: artifact '{aid}' has a how_to command "
                                  f"with no `os` label")
                elif isinstance(label, str) and not _is_platform_only(label):
                    errors.append(
                        f"{slug}: artifact '{aid}' has the `os` label "
                        f"{label!r}, which says more than the platform. Give "
                        f"it as a {{ja, en}} pair — a bare string is only for "
                        f"names that are identical in both languages")

        for h in ladder:
            if h.get("level") not in HINT_COSTS:
                errors.append(f"{slug}: artifact '{aid}' authors a hint with unknown "
                              f"level '{h.get('level')}'")
            if int(h.get("cost", 0)) != 0:
                errors.append(f"{slug}: artifact '{aid}' authors a hint with a cost")
        if not a.get("note"):
            errors.append(f"{slug}: source '{aid}' has no note — it is what the "
                          f"page says about how to read this source")
        if a.get("external_url") and not a.get("tool_name"):
            errors.append(f"{slug}: launch point '{aid}' names no tool")
        runnable = a.get("run_it_yourself")
        if runnable and not (how or {}).get("commands"):
            errors.append(f"{slug}: '{aid}' is marked run_it_yourself but its "
                          f"how_to lists no commands, so the page withholds the "
                          f"saved copy and offers nothing to run instead")
        if isinstance(runnable, str) and external.placeholder_key(runnable) is None:
            errors.append(f"{slug}: '{aid}' names its run_it_yourself "
                          f"dependency as a literal. Use a ${{placeholder}} "
                          f"declared in the external manifest")
        if not any(h.get("level") == "orientation" for h in ladder):
            errors.append(f"{slug}: source '{aid}' has no authored 'orientation' "
                          f"rung. It is the one that says WHERE INSIDE this source "
                          f"to look, and it cannot be composed from the note, which "
                          f"the page already prints")
        if not any(h.get("level") == "recovery" for h in ladder):
            errors.append(f"{slug}: source '{aid}' has no authored 'recovery' rung. "
                          f"Every other rung can be composed; this one has to be "
                          f"true about this source")
        if not (a.get("fallback_snapshot") or a.get("path")):
            errors.append(f"{slug}: source '{aid}' has no saved copy behind it, so "
                          f"nothing can be scored or recovered from it")

    seen_r = set()
    for r in m.get("rubric") or []:
        key = r.get("key")
        if not key:
            errors.append(f"{slug}: a rubric item has no key")
        elif key in seen_r:
            errors.append(f"{slug}: duplicate rubric key '{key}'")
        else:
            seen_r.add(key)
        if key in seen_q:
            errors.append(f"{slug}: rubric key '{key}' collides with a question key")
        response_key = r.get("response_key")
        if response_key is not None:
            if response_key not in seen_q:
                errors.append(f"{slug}: rubric '{key}' names response_key "
                              f"'{response_key}', which is not a question in this mission")
            else:
                answered = next((q for q in m.get("questions") or []
                                 if q.get("key") == response_key), {})
                if answered.get("type") in AUTO_TYPES:
                    errors.append(f"{slug}: rubric '{key}' grades '{response_key}', which "
                                  f"is auto-scored; a human would be marking it twice")
    return errors


def validate_all(content_dir, artifact_dir):
    errors = []
    missions = load_missions(content_dir)
    if not missions:
        return [f"no mission files found under {os.path.join(content_dir, 'missions')}"]
    slugs = set()
    for m in missions.values():
        slug = m.get("slug", "?")
        if slug in slugs:
            errors.append(f"duplicate mission slug '{slug}'")
        slugs.add(slug)
        errors += validate_structure(m, artifact_dir)
        errors += validate_budgets(m)
        errors += validate_language_coverage(m, slug)
    errors += external.validate(content_dir, artifact_dir)
    errors += external.validate_references(content_dir, list(missions.values()))
    return errors


def rotate_options(seed_key, index, options):
    n = len(options)
    if n < 2:
        return list(options)
    correct = next((i for i, o in enumerate(options) if o.get("correct")), 0)
    offset = int(hashlib.sha256(seed_key.encode("utf-8")).hexdigest()[:8], 16)
    target = (index + offset) % n
    shift = (correct - target) % n
    return options[shift:] + options[:shift]


def render_artifact_meta(a, lang, content_dir=None):
    out = {
        "id": a["id"],
        "type": a.get("type", "text"),
        "title": tx(a.get("title"), lang),
        "note": tx(a.get("note"), lang),
        "icon": a.get("icon", "▤"),
        "tool": a.get("tool"),
        "external": None,
        "how_to": render_how_to(a, lang, content_dir),
        "hints": [render_hint_meta(h, lang) for h in a.get("hints") or []],
    }
    if a.get("external_url") and content_dir:
        resolved = external.resolve(content_dir, a["external_url"])
        out["external"] = dict(resolved,
                               tool_name=a.get("tool_name") or resolved.get("tool_name"),
                               expected_pivot=tx(a.get("expected_pivot"), lang),
                               safety_notice=tx(a.get("safety_notice"), lang))
    return out


PLATFORM_WORDS = {"macos", "linux", "windows", "powershell", "cmd", "bash",
                  "zsh", "terminal", "wsl"}


def _is_platform_only(label):
    """True if this label names platforms and shells and says nothing more."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", label) if w]
    return bool(words) and all(w.lower() in PLATFORM_WORDS for w in words)


def run_it_yourself_is_live(entry, content_dir=None):
    flag = entry.get("run_it_yourself")
    if not flag:
        return False
    if flag is True or not content_dir:
        return bool(flag)
    key = external.placeholder_key(flag)
    if key is None:
        return True
    return bool(external.resolve(content_dir, flag).get("published"))


def render_how_to(a, lang, content_dir=None):
    block = a.get("how_to")
    if not block:
        return None
    tool_link = None
    if block.get("tool_link") and content_dir:
        resolved = external.resolve(content_dir, block["tool_link"])
        if resolved.get("published"):
            tool_link = {"url": resolved["url"],
                         "name": resolved.get("tool_name") or block.get("tool")}
    return {
        "tool_link": tool_link,
        "tool": tx(block.get("tool") or a.get("tool_name") or a.get("tool"),
                   lang),
        "steps": [
            ({"say": tx(s.get("say"), lang),
              "os": tx(s.get("os"), lang),
              "cmd": s.get("cmd")}
             if isinstance(s, dict) and ("cmd" in s or "say" in s)
             else tx(s, lang))
            for s in block.get("steps") or []
        ],
        "commands": [{"os": tx(c.get("os"), lang), "cmd": c.get("cmd")}
                     for c in block.get("commands") or []],
        "no_install": tx(block.get("no_install"), lang),
    }


def render_hint_meta(h, lang):
    return {"id": h["id"], "level": h["level"], "cost": int(h["cost"]),
            "unlock_after": hint_unlock_after(h),
            "label": tx(h.get("label"), lang)}


def facilitator_points_for(m, question_key):
    return float(sum(r.get("points", 0) for r in (m.get("rubric") or [])
                     if r.get("response_key") == question_key))


def render_question(m, q, index, lang):
    """One question, with nothing in it that could answer itself."""
    out = {
        "key": q["key"],
        "type": q["type"],
        "category": q.get("category", "correctness"),
        "points": float(q.get("points", 0)),
        "evidence_points": float(q.get("evidence_points", 0)),
        "prompt": tx(q.get("prompt"), lang),
        "help": tx(q.get("help"), lang),
        "placeholder": tx(q.get("placeholder"), lang),
        "evidence_required": bool(q.get("evidence_points")),
        "needs_confidence": bool(q.get("needs_confidence")),
        "needs_reasoning": bool(q.get("needs_reasoning")),
        "facilitator_points": facilitator_points_for(m, q["key"]),
        "hints": [{"id": h["id"], "level": h["level"]}
                  for h in question_ladder(q)],
        "accepted_evidence": [str(a) for a in (q.get("accepted_evidence") or [])],
    }
    if q["type"] in ("choice", "multi_choice"):
        out["options"] = [
            {"id": o["id"], "label": tx(o.get("label"), lang),
             "link": o.get("link")}
            for o in rotate_options(f"{m['slug']}:{q['key']}", index, q.get("options") or [])
        ]
    if q["type"] == "order":
        out["items"] = [{"id": o["id"], "label": tx(o.get("label"), lang)}
                        for o in q.get("items") or []]
    if q["type"] == "graph":
        out["nodes"] = [{"id": o["id"], "label": tx(o.get("label"), lang),
                         "kind": o.get("kind", "entity")}
                        for o in q.get("nodes") or []]
        out["relations"] = [{"id": o["id"], "label": tx(o.get("label"), lang)}
                            for o in q.get("relations") or []]
    return out


def render_mission(m, lang, content_dir=None):
    if m is None:
        return None
    return {
        "slug": m["slug"],
        "kind": m.get("kind", "mission"),
        "order": m.get("order", 0),
        "version": m.get("version"),
        "duration_seconds": int(m.get("duration_seconds", 1800)),
        "max_points": float(m.get("max_points", 0)),
        "budget": {k: float((m.get("budget") or {}).get(k, 0)) for k in CATEGORY_KEYS},
        "title": tx(m.get("title"), lang),
        "subtitle": tx(m.get("subtitle"), lang),
        "narrative": tx(m.get("narrative"), lang),
        "objectives": tx_list(m.get("objectives"), lang),
        "first_step": tx(m.get("first_step"), lang),
        "loop": tx_list(m.get("loop"), lang),
        "phases": [{"label": tx(step, lang), "seconds": int(step["seconds"])}
                   for step in (m.get("loop") or [])
                   if isinstance(step, dict) and step.get("seconds")],
        "artifacts": [render_artifact_meta(a, lang, content_dir)
                      for a in m.get("artifacts") or []],
        "questions": [render_question(m, q, i, lang)
                      for i, q in enumerate(m.get("questions") or [])],
        "hints": [render_hint_meta(h, lang) for h in m.get("hints") or []],
        "rubric": [{"key": r["key"], "points": float(r.get("points", 0)),
                    "category": r.get("category", "reasoning"),
                    "prompt": tx(r.get("prompt"), lang),
                    "placeholder": tx(r.get("placeholder"), lang)}
                   for r in m.get("rubric") or []],
    }


PIVOT_LEVELS = ("orientation", "recovery")


def pivot_ladder(artifact, lang, content_dir):
    from . import ui

    authored = {h.get("level"): h for h in (artifact.get("hints") or [])}
    aid = artifact["id"]

    def rung(level, label_key):
        h = authored.get(level) or {}
        return {
            "id": pivot_hint_id(aid, level),
            "level": level,
            "cost": 0,
            "artifact_id": aid,
            "unlock_after": 0,
            "label": ui.t(content_dir, label_key, lang),
            "text": tx(h.get("text"), lang),
        }

    return [rung("orientation", "pivot_hint.orientation"),
            rung("recovery", "pivot_hint.recovery")]


def all_hints(m, lang="ja", content_dir=None):
    out = list(m.get("hints") or [])
    for a in m.get("artifacts") or []:
        if not a.get("external_url"):
            continue
        if content_dir:
            out.extend(pivot_ladder(a, lang, content_dir))
        else:
            out.extend({"id": pivot_hint_id(a["id"], level), "level": level, "cost": 0,
                        "artifact_id": a["id"], "unlock_after": 0}
                       for level in PIVOT_LEVELS)
    return out


def find_hint(m, hint_id, lang="ja", content_dir=None):
    aid, level = parse_pivot_hint_id(hint_id)
    if aid is not None:
        artifact = find_artifact(m, aid)
        if artifact is None or level not in PIVOT_LEVELS:
            return None
        if content_dir is None:
            return {"id": hint_id, "level": level, "cost": 0, "artifact_id": aid,
                    "unlock_after": 0}
        for rung in pivot_ladder(artifact, lang, content_dir):
            if rung["id"] == hint_id:
                return rung
        return None
    for h in m.get("hints") or []:
        if h["id"] == hint_id:
            return h
    for q in m.get("questions") or []:
        for h in q.get("hints") or []:
            if h["id"] == hint_id:
                return h
    return None


def question_ladder(question):
    return list(question.get("hints") or [])


def hint_body(m, hint_id, lang, content_dir=None):
    h = find_hint(m, hint_id, lang, content_dir)
    if h is None:
        return None
    return {"id": h["id"], "level": h["level"], "cost": int(h["cost"]),
            "unlock_after": hint_unlock_after(h), "text": tx(h.get("text"), lang)}


def find_question(m, key):
    for q in m.get("questions") or []:
        if q["key"] == key:
            return q
    return None


def find_rubric(m, key):
    for r in m.get("rubric") or []:
        if r["key"] == key:
            return r
    return None


def find_artifact(m, artifact_id):
    for a in m.get("artifacts") or []:
        if a["id"] == artifact_id:
            return a
    return None


def artifact_ids(content_dir):
    """Every artifact id across every mission, for reference validation."""
    ids = set()
    for m in load_missions(content_dir).values():
        for a in m.get("artifacts") or []:
            ids.add(a["id"])
    return ids
