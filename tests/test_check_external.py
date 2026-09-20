import pathlib
import shutil

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "content" / "external_resources.yaml"


@pytest.fixture()
def script(monkeypatch, tmp_path):
    """The script, pointed at a throwaway copy of the manifest."""
    import importlib.util

    content_dir = tmp_path / "content"
    content_dir.mkdir()
    shutil.copy(MANIFEST, content_dir / "external_resources.yaml")

    spec = importlib.util.spec_from_file_location(
        "check_external", ROOT / "scripts" / "check_external.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "CONTENT_DIR", str(content_dir))
    module._manifest = content_dir / "external_resources.yaml"
    return module


def statuses(path):
    tree = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {r["key"]: r["status"] for r in tree["resources"]}


def test_a_blackhole_is_not_reported_as_a_network_error(script):
    assert "0.0.0.0" in script.BLACKHOLE
    assert "::" in script.BLACKHOLE


def test_a_redirect_counts_as_reachable(script):
    for code in (200, 301, 302, 307, 308):
        assert code in script.OK_CODES


def test_it_says_who_is_knocking(script):
    assert "predwen" in script.UA.lower()


def test_it_knows_which_urls_a_missing_file_check_applies_to(script):
    for names_one in (
            "https://archive.sakura-vpn-update.com/evidence/x/github-audit.json",
            "https://archive.sakura-vpn-update.com/logs/transfer.log",
            "https://cdn.sakura-vpn-update.com/releases/pkg_4.2.1.bin",
            "https://a.example/evidence/file-timeline.csv"):
        assert script.names_a_file(names_one), names_one

    for does_not in (
            "https://update.sakura-vpn-update.com/",            # the root
            "https://archive.sakura-vpn-update.com/evidence/SR-DEV-077/",
            "https://lookup.icann.org/en/lookup?name=example.com",
            "https://github.com/sora-dev77",
            "https://archive.sakura-vpn-update.com/reports/SBX-K42-20260902-01"):
        assert not script.names_a_file(does_not), does_not


def test_the_fingerprint_is_taken_once_per_host(script, monkeypatch):
    """One extra request per host, not one per resource."""
    calls = []

    class Fake:
        status = 200

        def read(self):
            return b"the not-found page"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_open(request, **kwargs):
        calls.append(request.full_url)
        return Fake()

    monkeypatch.setattr(script.urllib.request, "urlopen", fake_open)
    script._soft404.clear()

    first = script.soft404_fingerprint("host.example")
    second = script.soft404_fingerprint("host.example")
    assert first == second
    assert len(calls) == 1, calls
    assert "predwen-availability-probe" in calls[0]


def test_a_host_that_404s_properly_gets_no_fingerprint(script, monkeypatch):
    import urllib.error

    def fake_open(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found",
                                     None, None)

    monkeypatch.setattr(script.urllib.request, "urlopen", fake_open)
    script._soft404.clear()
    assert script.soft404_fingerprint("honest.example") is None


class Response:
    """Enough of an HTTP response for `probe()`, with real header semantics."""

    def __init__(self, body, status=200, headers=None):
        self.status = status
        self._body = body
        import email.message
        self.headers = email.message.Message()
        for name, value in (headers or {"Content-Type": "application/json"}).items():
            self.headers[name] = value

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def canonical(script, tmp_path, monkeypatch):
    """A resource whose live URL is a file we hold the canonical copy of."""
    artifacts = tmp_path / "artifacts"
    (artifacts / "digital-footprint").mkdir(parents=True)
    (artifacts / "digital-footprint" / "token_audit.json").write_bytes(
        b'{"source": "audit", "events": []}')
    monkeypatch.setattr(script, "ARTIFACT_DIR", str(artifacts))
    return {"fallback_snapshot": "digital-footprint/token_audit.json"}


def serve(script, monkeypatch, body, status=200, headers=None):
    def fake_open(request, **kwargs):
        if "predwen-availability-probe" in request.full_url:
            return Response(b"<!doctype html><html>not found</html>", 200,
                            {"Content-Type": "text/html"})
        if status >= 400:
            import urllib.error
            raise urllib.error.HTTPError(request.full_url, status, "err",
                                         None, None)
        return Response(body, status, headers)

    monkeypatch.setattr(script.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(script, "addresses", lambda host: ["203.0.113.9"])
    script._soft404.clear()


URL = "https://archive.example/evidence/SR-IR-2026-0902/github-audit.json"


def test_the_correct_file_passes(script, canonical, monkeypatch):
    serve(script, monkeypatch, b'{"source": "audit", "events": []}')
    verdict, _, _ = script.probe(URL, canonical)
    assert verdict == 200


def test_a_genuine_404_is_reported(script, canonical, monkeypatch):
    serve(script, monkeypatch, b"", status=404)
    verdict, _, _ = script.probe(URL, canonical)
    assert verdict == 404


def test_200_with_the_fallback_html_is_caught(script, canonical, monkeypatch):
    serve(script, monkeypatch, b"<!doctype html><html>not found</html>", 200,
          {"Content-Type": "text/html"})
    verdict, _, detail = script.probe(URL, canonical)
    assert verdict == "WRONG-BYTES"
    assert "fallback HTML page" in detail


def test_200_with_the_wrong_file_is_caught(script, canonical, monkeypatch):
    """Right name, right status, wrong evidence."""
    serve(script, monkeypatch, b'{"source": "somebody elses log"}')
    verdict, _, detail = script.probe(URL, canonical)
    assert verdict == "WRONG-BYTES"
    assert "different content" in detail


def test_stale_content_with_a_different_hash_is_caught(script, canonical,
                                                       monkeypatch):
    serve(script, monkeypatch, b'{"source": "audit", "events": [] }')
    verdict, _, _ = script.probe(URL, canonical)
    assert verdict == "WRONG-BYTES"


def test_json_arriving_as_html_is_caught(script, canonical, monkeypatch):
    serve(script, monkeypatch, b"<html><body>oops</body></html>", 200,
          {"Content-Type": "text/html; charset=utf-8"})
    verdict, _, _ = script.probe(URL, canonical)
    assert verdict == "WRONG-BYTES"


def test_header_names_are_read_case_insensitively(script, canonical,
                                                  monkeypatch):
    serve(script, monkeypatch, b'{"source": "audit", "events": []}', 200,
          {"content-type": "application/json"})
    verdict, _, detail = script.probe(URL, canonical)
    assert verdict == 200
    assert detail == "application/json", "the header was read case-sensitively"

    source = (ROOT / "scripts" / "check_external.py").read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
    assert "dict(r.headers)" not in code, \
        "a plain dict loses the case-insensitive lookup"


def test_a_page_about_the_evidence_is_not_compared(script, monkeypatch,
                                                   tmp_path):
    entry = {"fallback_snapshot": "digital-footprint/commit_85902d17.txt"}
    for url in ("https://github.com/x/y/commit/85902d17",
                "https://archive.example/evidence/SR-DEV-077/",
                "https://lookup.icann.org/en/lookup?name=example.com"):
        assert script.serves_its_own_copy(entry, url) is None, url


def test_a_local_resolver_answering_zero_is_not_an_outage(script, monkeypatch):
    monkeypatch.setattr(script, "addresses", lambda host: ["0.0.0.0"])
    verdict, _, detail = script.probe(URL, {})
    assert verdict == "BLACKHOLE"
    assert "this resolver" in detail
    assert "DNS-over-HTTPS" in detail
    assert "deleted" not in detail.lower()


def test_set_pending_flips_only_what_it_is_given(script):
    path = script._manifest
    before = statuses(path)
    assert before["m1_repository"] == "ready"

    changed = script.set_pending(["m1_repository", "m2_payload"])

    after = statuses(path)
    assert sorted(changed) == ["m1_repository", "m2_payload"]
    assert after["m1_repository"] == "pending"
    assert after["m2_payload"] == "pending"
    for key, status in before.items():
        if key not in ("m1_repository", "m2_payload"):
            assert after[key] == status, key


def test_it_never_touches_a_retired_resource(script):
    """`retired` means somebody decided. This is not the tool to overrule it."""
    path = script._manifest
    text = path.read_text(encoding="utf-8").replace(
        "  - key: m1_photo\n    tool_name: ExifTool\n    resource_type: first_party\n    status: ready",
        "  - key: m1_photo\n    tool_name: ExifTool\n    resource_type: first_party\n    status: retired")
    path.write_text(text, encoding="utf-8", newline="\n")

    script.set_pending(["m1_photo"])
    assert statuses(path)["m1_photo"] == "retired"


def test_it_is_a_no_op_on_something_already_pending(script):
    path = script._manifest
    assert script.set_pending(["m1_photo"]) == ["m1_photo"]
    assert statuses(path)["m1_photo"] == "pending"
    before = path.read_text(encoding="utf-8")

    assert script.set_pending(["m1_photo"]) == []
    assert statuses(path)["m1_photo"] == "pending"
    assert path.read_text(encoding="utf-8") == before, \
        "a no-op rewrote the manifest"


def test_it_changes_exactly_one_line_per_resource(script):
    path = script._manifest
    before = path.read_text(encoding="utf-8").split("\n")
    script.set_pending(["m1_repository"])
    after = path.read_text(encoding="utf-8").split("\n")

    assert len(before) == len(after), "a line was added or removed"
    differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(differing) == 1, differing
    assert before[differing[0]].strip() == "status: ready"
    assert after[differing[0]].strip() == "status: pending"


def test_there_is_no_reverse(script):
    source = (ROOT / "scripts" / "check_external.py").read_text(encoding="utf-8")
    assert "set_ready" not in source
    assert '"status: pending", "status: ready"' not in source
    assert "--set-ready" not in source
