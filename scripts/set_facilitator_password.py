import argparse
import getpass
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import (  # noqa: E402
    check_password_hash,
    generate_password_hash,
)

FACILITATOR_KEY = "SECCAP_FACILITATOR_PASSWORD_HASH"
ASSISTANT_KEY = "SECCAP_ASSISTANT_PASSWORD_HASH"
KEY = FACILITATOR_KEY
ROOT = pathlib.Path(__file__).resolve().parents[1]

MIN_LENGTH = 12


def require_a_terminal():
    if sys.stdin is not None and sys.stdin.isatty():
        return
    raise SystemExit(
        "This needs a real terminal: it asks for the password without echoing "
        "it, and cannot do that through a pipe or an IDE output pane.\n"
        "Open PowerShell or cmd in the project folder and run it there:\n"
        "    .venv\\Scripts\\python.exe scripts/set_facilitator_password.py")


def ask():
    """Twice, unechoed, and refuse the ones that will be regretted."""
    require_a_terminal()
    who = "assistant" if KEY == ASSISTANT_KEY else "facilitator"
    while True:
        first = getpass.getpass(f"New {who} password: ")
        if len(first) < MIN_LENGTH:
            print(f"  too short — use at least {MIN_LENGTH} characters.")
            continue
        if first != getpass.getpass("Repeat it: "):
            print("  they do not match.")
            continue
        return first


def rewrite(path, digest):
    if not path.exists():
        raise SystemExit(f"{path} does not exist. Copy .env.example to .env first.")

    original = path.read_text(encoding="utf-8")
    line = f"{KEY}={digest}"
    pattern = re.compile(rf"^{KEY}=.*$", re.MULTILINE)

    if pattern.search(original):
        updated = pattern.sub(lambda _m: line, original, count=1)
        what = "replaced"
    else:
        sep = "" if original.endswith("\n") or not original else "\n"
        updated = f"{original}{sep}{line}\n"
        what = "appended"

    path.write_text(updated, encoding="utf-8", newline="")
    return what


def current_hash(path):
    """The hash the file holds, or "" — the same string app/config.py reads."""
    if not path.exists():
        raise SystemExit(f"{path} does not exist.")
    found = re.search(rf"^{KEY}=(.*)$", path.read_text(encoding="utf-8"),
                      re.MULTILINE)
    return found.group(1).strip() if found else ""


MANGLERS = (
    (lambda s: re.sub(r"\$[A-Za-z_][A-Za-z0-9_]*", "", s),
     "PowerShell or a POSIX shell expanded a $variable inside the double quotes"),
    (lambda s: re.sub(r"\$\{[^}]*\}", "", s),
     "a shell expanded a ${...} reference inside the double quotes"),
    (lambda s: s.replace("`", ""),
     "PowerShell ate a backtick, which is its escape character"),
    (lambda s: s.replace("\\", ""),
     "a POSIX shell ate a backslash"),
    (lambda s: re.sub(r"%[A-Za-z_][A-Za-z0-9_]*%", "", s),
     "cmd.exe expanded a %VARIABLE%"),
    (lambda s: s.split("$")[0],
     "everything from the first $ onwards was lost to shell expansion"),
    (lambda s: s.strip(),
     "a leading or trailing space was included when the hash was made"),
    (lambda s: s + "\n",
     "a trailing newline was included when the hash was made"),
)


def diagnose(digest, candidate):
    for mangle, explanation in MANGLERS:
        try:
            altered = mangle(candidate)
        except Exception:                                     # noqa: BLE001
            continue
        if altered == candidate:
            continue                    # nothing to mangle; not this one
        try:
            if check_password_hash(digest, altered):
                return explanation
        except Exception:                                     # noqa: BLE001
            return None
    return None


def verify(path):
    """Answer "is it my password that is wrong?" without changing anything."""
    digest = current_hash(path)
    if not digest:
        print(f"{path} has no {KEY}. Facilitator access is disabled entirely,"
              " and no password would work. Run this script without --verify.")
        return 1

    require_a_terminal()
    candidate = getpass.getpass("Password to check: ")
    try:
        ok = check_password_hash(digest, candidate)
    except Exception as exc:                                  # noqa: BLE001
        print(f"\nThe stored hash is malformed and nothing can match it:"
              f" {type(exc).__name__}. Run this script without --verify to set"
              " a new one.")
        return 1

    if ok:
        print("\nThat is the password. If signing in still fails, the problem"
              " is not the password:")
        print("  * check the application was restarted after .env last changed")
        print("  * check you are on /facilitator, not /admin")
        return 0

    print("\nThat is NOT the password stored in this file.")

    cause = diagnose(digest, candidate)
    if cause:
        print("\nBUT IT IS THE PASSWORD YOU MEANT. What matches the stored hash")
        print("is a MANGLED form of what you just typed, because:")
        print(f"\n    {cause}.")
        print("\nThe hash was created by putting the password on a shell command")
        print("line inside double quotes, and the shell rewrote it before Python")
        print("saw it. You have been typing the real password into a hash made")
        print("from a string nobody ever typed.")
        print("\nRun this script without --verify. It reads the password from a")
        print("prompt instead of a command line, so no shell can touch it.")
        return 1

    print("A scrypt hash is one-way, so it cannot be read back. Run this"
          " script without --verify to set a new one.")
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--print", dest="show", action="store_true",
                        help="print the hash instead of writing any file")
    parser.add_argument("--verify", action="store_true",
                        help="check a password against the stored hash;"
                             " changes nothing")
    parser.add_argument("--file", default=str(ROOT / ".env"),
                        help="env file to rewrite (default: .env)")
    parser.add_argument("--assistant", action="store_true",
                        help="set the teaching assistants' password "
                             f"({ASSISTANT_KEY}) instead of the facilitator's")
    args = parser.parse_args()

    global KEY
    KEY = ASSISTANT_KEY if args.assistant else FACILITATOR_KEY

    if args.verify:
        raise SystemExit(verify(pathlib.Path(args.file)))

    digest = generate_password_hash(ask())

    if args.show:
        print(f"\n{KEY}={digest}")
        print("\nSet that in the environment and restart the application.")
        return

    path = pathlib.Path(args.file)
    what = rewrite(path, digest)

    if current_hash(path) != digest:
        raise SystemExit(f"the hash did not survive being written to {path}")

    print(f"\n{what} {KEY} in {path}, and read it back intact.")
    print("\nRESTART THE APPLICATION NOW. The hash is read once, at start-up,")
    print("so a server that is already running still holds the old one — which")
    print("looks exactly like the new password not working.")
    print("\nThen sign in at " + ("/assistant." if args.assistant else "/facilitator."))


if __name__ == "__main__":
    main()
