import argparse
import hashlib
import os
import socket
import ssl
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import external              # noqa: E402
from app.config import ARTIFACT_DIR, CONTENT_DIR    # noqa: E402

TIMEOUT = 20
OK_CODES = (200, 301, 302, 303, 307, 308)
BLACKHOLE = ("0.0.0.0", "::", "127.0.0.1", "::1")

UA = "predwen-seccap-availability-check"


def addresses(host):
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


def serves_its_own_copy(entry, url):
    rel = entry.get("fallback_snapshot")
    if not rel or not names_a_file(url):
        return None
    path = os.path.join(ARTIFACT_DIR, rel.replace("/", os.sep))
    return path if os.path.isfile(path) else None


def names_a_file(url):
    path = urlparse(url).path
    if urlparse(url).query or path in ("", "/") or path.endswith("/"):
        return False
    stem, dot, extension = path.rpartition("/")[2].rpartition(".")
    return bool(stem and dot and 1 <= len(extension) <= 5 and extension.isalnum())


_soft404 = {}


def soft404_fingerprint(host, scheme="https"):
    if host in _soft404:
        return _soft404[host]
    probe_url = f"{scheme}://{host}/predwen-availability-probe-4f1c9a2e.json"
    request = urllib.request.Request(probe_url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT,
                                    context=ssl.create_default_context()) as r:
            body = hashlib.sha256(r.read()).hexdigest() if r.status < 300 else None
    except Exception:                                       # noqa: BLE001
        body = None
    _soft404[host] = body
    return body


def probe(url, entry=None):
    """(verdict, addresses, detail). `verdict` is a status code or a word."""
    host = urlparse(url).hostname
    if not host:
        return "BAD-URL", [], url
    try:
        addrs = addresses(host)
    except OSError as exc:
        return "DNS-FAIL", [], str(exc)
    if all(a in BLACKHOLE for a in addrs):
        return ("BLACKHOLE", addrs,
                "this resolver answers 0.0.0.0 — check with DNS-over-HTTPS "
                "before concluding the records are gone")
    request = urllib.request.Request(url, method="GET",
                                     headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT,
                                    context=ssl.create_default_context()) as r:
            body = r.read()
            content_type = r.headers.get("Content-Type", "")

            if r.status < 300:
                local = serves_its_own_copy(entry or {}, url)
                if local:
                    with open(local, "rb") as handle:
                        expected = handle.read()
                    if hashlib.sha256(body).hexdigest() != \
                            hashlib.sha256(expected).hexdigest():
                        looks_html = body.lstrip()[:15].lower().startswith(
                            (b"<!doctype", b"<html"))
                        return ("WRONG-BYTES", addrs,
                                f"HTTP 200 but not the file the answer key was "
                                f"written against — "
                                f"{'the fallback HTML page' if looks_html else 'different content'}"
                                f" ({len(body)} bytes live, {len(expected)} local)")
                    return r.status, addrs, content_type

                for name in (entry or {}).get("expect_headers") or []:
                    if not r.headers.get(name):
                        return ("NO-HEADER", addrs,
                                f"HTTP {r.status}, but the response carries no "
                                f"{name} — that header is where the answer is "
                                f"read from, and it is the only place it exists")

                if names_a_file(url):
                    missing = soft404_fingerprint(host, urlparse(url).scheme)
                    if missing and hashlib.sha256(body).hexdigest() == missing:
                        return ("MISSING", addrs,
                                "HTTP 200, but the same bytes this host "
                                "returns for a path that does not exist")
            return r.status, addrs, content_type
    except urllib.error.HTTPError as exc:
        return exc.code, addrs, exc.reason or ""
    except Exception as exc:                      # noqa: BLE001
        return "ERROR", addrs, f"{type(exc).__name__}: {exc}"[:90]


def set_pending(keys):
    path = os.path.join(CONTENT_DIR, external.MANIFEST)
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().split("\n")

    wanted, changed = set(keys), []
    current = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("- key:"):
            current = stripped.split(":", 1)[1].strip()
        elif current in wanted and stripped == "status: ready":
            lines[i] = line.replace("status: ready", "status: pending")
            changed.append(current)
            current = None

    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))
    external.clear_cache()
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--required-only", action="store_true",
                        help="skip the entries marked optional")
    parser.add_argument("--set-pending", action="store_true",
                        help="mark every unreachable required resource "
                             "`status: pending` in the manifest")
    args = parser.parse_args()

    resources = external.load(CONTENT_DIR, force=True)
    rows = []
    for key, entry in sorted(resources.items()):
        if args.required_only and entry.get("optional"):
            continue
        url = entry.get("url") or ""
        verdict, addrs, detail = probe(url, entry)
        rows.append({
            "key": key,
            "declared": entry.get("status"),
            "optional": bool(entry.get("optional")),
            "verdict": verdict,
            "addrs": addrs,
            "detail": detail,
            "url": url,
        })

    width = max((len(r["key"]) for r in rows), default=10)
    broken, tolerated = [], []
    print()
    for r in rows:
        up = r["verdict"] in OK_CODES
        expected_down = r["declared"] in ("pending", "retired")
        if not up:
            r["expected_down"] = expected_down
            (tolerated if expected_down or r["optional"] else broken).append(r)
        mark = "ok  " if up else ("note" if expected_down or r["optional"]
                                  else "DOWN")
        print(f"  {mark} {r['key']:<{width}}  {str(r['declared']):<8} "
              f"{str(r['verdict']):<10} {r['url']}")
        if not up and r["detail"]:
            print(f"       {'':<{width}}  {r['detail']}")

    print(f"\n  {len(rows) - len(broken) - len(tolerated)} reachable, "
          f"{len(broken)} broken, {len(tolerated)} down but declared so, "
          f"{len(rows)} checked")

    declared = [r for r in tolerated if r.get("expected_down")]
    flapping = [r for r in tolerated if not r.get("expected_down")]

    if declared:
        print("\n  Down, and the manifest already says so — no action needed:")
        for r in declared:
            print(f"    - {r['key']} ({r['declared']}"
                  f"{', optional' if r['optional'] else ''})")

    if flapping:
        print("\n  Optional, declared ready, and NOT ANSWERING right now.")
        print("  No graded answer depends on any of these and no mission links")
        print("  them, so the exercise runs. What a team pressing one meets is")
        print("  that service's own error page:")
        for r in flapping:
            print(f"    - {r['key']:<14} {r['verdict']}  {r['url']}")
        print("  Leave it, and say so if somebody asks — a public service having")
        print("  a bad morning is what using a public service looks like. Mark it")
        print("  `pending` only if you would rather nothing offered it.")

    if broken:
        hosts = {}
        for r in broken:
            hosts.setdefault(urlparse(r["url"]).hostname, []).append(r["key"])
        print(f"\n  {len(broken)} resource(s) a mission depends on are "
              f"UNREACHABLE, on {len(hosts)} host(s):")
        for host, keys in sorted(hosts.items()):
            print(f"    {host}")
            for key in keys:
                print(f"      - {key}")
        if args.set_pending:
            changed = set_pending(r["key"] for r in broken)
            print(f"\n  --set-pending: {len(changed)} resource(s) marked "
                  f"`pending` in {external.MANIFEST}.")
            for key in changed:
                print(f"    - {key}")
            print("\n  Each of those pages now shows its saved copy with the "
                  "'not published'\n  banner instead of a launch button that "
                  "goes nowhere. Set them back to\n  `ready` — by hand, "
                  "deliberately — once this check is clean again.")
            return 1
        print("\n  Either bring the host back, or run this again with "
              "--set-pending, which\n  marks exactly these resources "
              "`status: pending` so the platform shows the\n  saved copy "
              "instead of a dead link.")
        return 1

    print("\n  every required external resource is reachable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
