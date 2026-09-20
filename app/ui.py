import os

import yaml

_cache = None
_stamp = None


def _path(content_dir):
    return os.path.join(content_dir, "ui.yaml")


def load(content_dir, force=False):
    global _cache, _stamp
    path = _path(content_dir)
    stamp = os.path.getmtime(path) if os.path.exists(path) else None
    if _cache is None or force or stamp != _stamp:
        if stamp is None:
            _cache, _stamp = {}, None
            return _cache
        with open(path, encoding="utf-8") as f:
            _cache = yaml.safe_load(f) or {}
        _stamp = stamp
    return _cache


def clear_cache():
    global _cache, _stamp
    _cache, _stamp = None, None


def lookup(tree, key):
    node = tree
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def t(content_dir, key, lang, **fmt):
    node = lookup(load(content_dir), key)
    if node is None:
        return f"⟦{key}⟧"
    if isinstance(node, str):
        text = node
    elif isinstance(node, dict):
        text = node.get("en" if lang == "en" else "ja") or node.get("ja") or node.get("en")
    else:
        return f"⟦{key}⟧"
    if text is None:
        return f"⟦{key}⟧"
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def t_list(content_dir, key, lang):
    node = lookup(load(content_dir), key)
    if not isinstance(node, list):
        return []
    out = []
    for item in node:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            out.append(item.get("en" if lang == "en" else "ja")
                       or item.get("ja") or item.get("en") or "")
    return out


def flat_keys(node, prefix=""):
    out = {}
    if isinstance(node, dict):
        if {"ja", "en"} & set(node):
            out[prefix] = node
            return out
        for key, value in node.items():
            out.update(flat_keys(value, f"{prefix}.{key}" if prefix else key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            out.update(flat_keys(value, f"{prefix}[{i}]"))
    return out


def validate(content_dir):
    """Both halves of every pair present and non-empty."""
    errors = []
    tree = load(content_dir, force=True)
    if not tree:
        return [f"ui.yaml missing or empty at {_path(content_dir)}"]
    for key, pair in flat_keys(tree).items():
        for half in ("ja", "en"):
            value = pair.get(half)
            if value is None:
                errors.append(f"ui.{key}: no '{half}' half")
            elif isinstance(value, str) and not value.strip():
                errors.append(f"ui.{key}: empty '{half}' half")
    return errors
