#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"

FILES = [CONTENT / "ui.yaml", CONTENT / "external_resources.yaml"]
FILES += sorted((CONTENT / "missions").glob("*.yaml"))

FORBIDDEN_JA = {
    "道具": "use ツール for software and tools",
    "持ち出し": "use 外部送信 for exfiltration in participant copy",
}
FORBIDDEN_EN = {
    "rather than": "use a direct instruction",
    "the whole point": "state the reason directly",
    "which is the point": "state the reason directly",
}

def pairs(node, path=""):
    if isinstance(node, dict):
        if ("ja" in node and "en" in node and isinstance(node["ja"], str)
                and isinstance(node["en"], str)):
            yield path or "<root>", node["ja"], node["en"]
        for key, value in node.items():
            yield from pairs(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from pairs(value, f"{path}[{index}]")


def placeholders(text):
    return sorted(re.findall(r"\{[A-Za-z_][A-Za-z0-9_]*\}", text))


def audit():
    findings = []
    for file in FILES:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
        rel = file.relative_to(ROOT)
        for path, ja, en in pairs(data):
            if placeholders(ja) != placeholders(en):
                findings.append(f"{rel}:{path}: placeholder mismatch")
            for phrase, note in FORBIDDEN_JA.items():
                if phrase in ja:
                    findings.append(f"{rel}:{path}: Japanese `{phrase}` — {note}")
            low = en.lower()
            for phrase, note in FORBIDDEN_EN.items():
                if phrase in low:
                    findings.append(f"{rel}:{path}: English `{phrase}` — {note}")
    return findings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    findings = audit()
    if findings:
        print("Japanese wording audit failed:")
        for finding in findings:
            print(" -", finding)
        return 1

    print("Japanese wording audit: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
