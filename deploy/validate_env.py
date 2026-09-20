#!/usr/bin/env python3
import os
import re
import sys

VALUE_PATTERNS = {
    "SECCAP_SITE_ADDRESS": re.compile(
        r"\A(?::\d{1,5}"
        r"|[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
        r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+)\Z"),
    "SECCAP_BASIC_AUTH_USER": re.compile(r"\A[A-Za-z0-9._-]{1,64}\Z"),
    "SECCAP_BASIC_AUTH_HASH": re.compile(
        r"\A\$2[abxy]?\$\d{2}\$[./A-Za-z0-9]{53}\Z"),
    "SECCAP_SECRET_KEY": re.compile(r"\A[A-Za-z0-9_-]{32,512}\Z"),
    "SECCAP_FACILITATOR_PASSWORD_HASH": re.compile(
        r"\A(?:scrypt:\d{1,7}:\d{1,4}:\d{1,4}"
        r"|pbkdf2:[a-z0-9]{1,16}:\d{1,9})"
        r"\$[A-Za-z0-9./+=]{1,64}\$[0-9a-f]{32,256}\Z"),
    "SECCAP_ASSISTANT_PASSWORD_HASH": re.compile(
        r"\A(?:scrypt:\d{1,7}:\d{1,4}:\d{1,4}"
        r"|pbkdf2:[a-z0-9]{1,16}:\d{1,9})"
        r"\$[A-Za-z0-9./+=]{1,64}\$[0-9a-f]{32,256}\Z"),
    "SECCAP_IMAGE_TAG": re.compile(r"\A[A-Za-z0-9._-]{1,128}\Z"),
    "SECCAP_DEFAULT_LANG": re.compile(r"\A(?:ja|en)\Z"),
    "SECCAP_JOIN_RPM": re.compile(r"\A\d{1,5}\Z"),
    "SECCAP_BACKUP_DIR": re.compile(r"\A/[A-Za-z0-9._/-]{1,200}\Z"),
}

REQUIRED = (
    "SECCAP_SITE_ADDRESS",
    "SECCAP_BASIC_AUTH_USER",
    "SECCAP_BASIC_AUTH_HASH",
    "SECCAP_SECRET_KEY",
    "SECCAP_FACILITATOR_PASSWORD_HASH",
    "SECCAP_IMAGE_TAG",
)

PER_RUN_ONLY = {
    "SECCAP_RESTORE_CONFIRM":
        "it confirms overwriting a live database; a standing yes defeats it",
    "SECCAP_RESTORE_INJECT_FAILURE":
        "it exists only to make the CI failure test deterministic",
    "SECCAP_BASIC_AUTH_PLAINTEXT":
        "verify.sh takes it for one run; it is a password, not configuration",
    "SECCAP_ENV_FILE":
        "it names this file; setting it inside this file is circular",
}

ASSIGNMENT = re.compile(r"\A([A-Za-z_][A-Za-z0-9_]*)='([^'\n]*)'\Z")
COMMENT = re.compile(r"\A[ \t]*#")
BLANK = re.compile(r"\A[ \t]*\Z")


def problems(text):
    """Every reason the file is not acceptable. Text in, complaints out."""
    found = []
    seen = {}

    if "\x00" in text:
        found.append("the file contains a NUL byte")

    for number, line in enumerate(text.split("\n"), start=1):
        if line.endswith("\r"):
            found.append(
                "line %d ends with CR. This file is sourced by /bin/sh, which "
                "would put the carriage return inside the value and corrupt "
                "every hash in it. Convert it to Unix line endings." % number)
            continue
        if BLANK.match(line) or COMMENT.match(line):
            continue

        match = ASSIGNMENT.match(line)
        if not match:
            found.append(
                "line %d is not a single-quoted assignment: %s\n"
                "        Only blank lines, # comments and KEY='value' are "
                "accepted. This file is executed as shell." % (number, short(line)))
            continue

        key, value = match.group(1), match.group(2)

        if any(ord(c) < 32 for c in value):
            found.append("line %d: the value of %s contains a control character"
                         % (number, key))
            continue

        if key in seen:
            found.append("line %d: %s is assigned again (first at line %d). "
                         "The last one would silently win." % (number, key, seen[key]))
            continue
        seen[key] = number

        if key in PER_RUN_ONLY:
            found.append("line %d: %s does not belong in this file — %s"
                         % (number, key, PER_RUN_ONLY[key]))
            continue

        if key not in VALUE_PATTERNS:
            found.append("line %d: %s is not a key this deployment reads.\n"
                         "        Known keys: %s"
                         % (number, key, ", ".join(sorted(VALUE_PATTERNS))))
            continue

        if not VALUE_PATTERNS[key].match(value):
            found.append("line %d: the value of %s is not in the expected "
                         "format (%s)" % (number, key, describe(key)))

    for key in REQUIRED:
        if key not in seen:
            found.append("%s is missing" % key)

    return found


def describe(key):
    return {
        "SECCAP_SITE_ADDRESS": "a domain name, or :PORT",
        "SECCAP_BASIC_AUTH_USER": "letters, digits, dot, dash, underscore",
        "SECCAP_BASIC_AUTH_HASH": "a bcrypt hash from `caddy hash-password`",
        "SECCAP_SECRET_KEY": "at least 32 urlsafe characters",
        "SECCAP_FACILITATOR_PASSWORD_HASH": "a Werkzeug scrypt or pbkdf2 hash",
        "SECCAP_ASSISTANT_PASSWORD_HASH": "a Werkzeug scrypt or pbkdf2 hash",
        "SECCAP_IMAGE_TAG": "a Docker tag",
        "SECCAP_DEFAULT_LANG": "ja or en",
        "SECCAP_JOIN_RPM": "a number",
        "SECCAP_BACKUP_DIR": "an absolute path",
    }.get(key, "see deploy/validate_env.py")


def short(line):
    line = line.strip()
    return line if len(line) <= 60 else line[:57] + "..."


def main(path):
    if not os.path.isfile(path):
        print("validate_env: no such file: %s" % path, file=sys.stderr)
        return 1

    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        text = fh.read()

    found = problems(text)
    if found:
        print("validate_env: REFUSING %s" % path, file=sys.stderr)
        for problem in found:
            print("  - %s" % problem, file=sys.stderr)
        print("\nThis file is sourced by deploy/_lib.sh, so anything in it "
              "runs.\nRegenerate it with deploy/generate-secrets.sh rather "
              "than hand-editing.", file=sys.stderr)
        return 1

    print("validate_env: %s is inert (%d settings)"
          % (path, len([p for p in text.split("\n")
                        if ASSIGNMENT.match(p)])))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: validate_env.py <env-file>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
