import json
import pathlib

import pytest

from app import missions as content

from .conftest import join, open_all_sources, wind_forward

ROOT = pathlib.Path(__file__).resolve().parents[1]
DNS_FILE = ROOT / "artifacts" / "infrastructure" / "dns_snapshot.json"


def member(app, event, facilitator, slug):
    facilitator.post(f"/facilitator/mission/{slug}/open", {})
    wind_forward(app, event, slug)
    open_all_sources(app, event, slug)
    return join(app, event, "Team 1", "kenji")


def page(p, artifact_id):
    r = p.get(f"/artifacts/{artifact_id}")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def manifest(app, slug, artifact_id):
    with app.app_context():
        definition = content.get_mission(app.config["CONTENT_DIR"], slug)
    return content.find_artifact(definition, artifact_id)


def test_the_dns_page_does_not_print_the_addresses(app, event, facilitator):
    """The one assertion that matters: the answer is not on the page."""
    p = member(app, event, facilitator, "fake-infrastructure")
    html = page(p, "dns")

    addresses = set()

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str) and node.count(".") == 3:
            head = node.split(".")[0]
            if head.isdigit():
                addresses.add(node)

    walk(json.loads(DNS_FILE.read_text(encoding="utf-8")))
    assert addresses, "the fixture has no addresses in it — check this test"
    leaked = sorted(a for a in addresses if a in html)
    assert not leaked, f"the dns page prints the answer: {leaked}"


def test_it_shows_the_commands_instead(app, event, facilitator):
    p = member(app, event, facilitator, "fake-infrastructure")
    html = page(p, "dns")

    assert "dig +short update.sakura-vpn-update.com A" in html
    assert "Resolve-DnsName" in html
    assert "nslookup" in html
    for host in ("cdn.sakura-vpn-update.com", "archive.sakura-vpn-update.com"):
        assert host not in html, host
    assert "X-Sakura-Manifest" in html and "X-Sakura-Failover" in html


def test_the_mission_does_not_state_the_result_before_they_run_it():
    import yaml

    tree = yaml.safe_load((ROOT / "content" / "missions"
                           / "m2-fake-infrastructure.yaml").read_text(encoding="utf-8"))
    upfront = " ".join([
        (tree.get("narrative") or {}).get("en", ""),
        (tree.get("first_step") or {}).get("en", ""),
        " ".join((o.get("en") or "") for o in tree.get("objectives") or []),
    ]).lower()

    for claim in ("return the same address", "shared proxies", "shared proxy",
                  "the shared addresses", "same addresses"):
        assert claim not in upfront, (
            f"the mission states the DNS result before anybody resolves "
            f"anything: {claim!r}")
    for answer in ("common operator", "same operator"):
        assert answer not in upfront, (
            f"the briefing states the answer to m2_shared_ip: {answer!r}")
    shared = next(q for q in tree["questions"] if q["key"] == "m2_shared_ip")
    ladder = " ".join((h.get("text") or {}).get("en", "")
                      for h in shared.get("hints") or []).lower()
    assert "operator" in ladder


def test_the_bytes_are_refused_too(app, event, facilitator):
    p = member(app, event, facilitator, "fake-infrastructure")
    r = p.get("/artifacts/dns/download")
    assert r.status_code == 409
    assert r.get_json()["error"] == "run_it_yourself"


def test_the_file_is_still_on_disk(app):
    assert DNS_FILE.exists()
    entry = manifest(app, "fake-infrastructure", "dns")
    assert entry["path"] == "infrastructure/dns_snapshot.json"
    assert entry["run_it_yourself"] == "${m2_update_service}"


@pytest.mark.parametrize("artifact_id,key", [
    ("token-audit", "m1_token_audit"),
    ("username-collision", "m1_username_collision"),
])
def test_the_last_two_are_launch_points_now(app, artifact_id, key):
    entry = manifest(app, "digital-footprint", artifact_id)
    assert entry.get("external_url") == "${%s}" % key
    assert entry.get("expected_pivot"), artifact_id
    assert "client_supplied" not in entry
    assert "withheld_snapshot" not in entry
    ui = (ROOT / "content" / "ui.yaml").read_text(encoding="utf-8")
    assert "\n  client_supplied:" not in ui
    assert "\n  withheld_snapshot:" not in ui


@pytest.mark.parametrize("artifact_id", ["token-audit", "username-collision"])
def test_they_hand_over_nothing_now_that_they_are_published(app, event,
                                                           facilitator,
                                                           artifact_id):
    p = member(app, event, facilitator, "digital-footprint")
    html = page(p, artifact_id)
    assert "Not published" not in html
    assert 'id="tree"' not in html, "the page is still printing its own evidence"
    assert "Open externally" in html
    assert p.get(f"/artifacts/{artifact_id}/download").status_code == 409


def test_the_two_new_resources_are_declared_and_have_their_file(app):
    from app import external

    with app.app_context():
        manifest_entries = external.load(app.config["CONTENT_DIR"])
    for key, rel in (("m1_token_audit", "digital-footprint/token_audit.json"),
                     ("m1_username_collision",
                      "digital-footprint/username_collision.json")):
        entry = manifest_entries[key]
        assert entry["status"] == "ready", key
        assert entry["fallback_snapshot"] == rel, key
        assert (ROOT / "artifacts" / rel).exists(), rel
        assert entry["url"].rsplit("/", 1)[-1] in ("github-audit.json",
                                                   "username-collision.json")


def test_no_other_source_serves_a_copy_it_should_not(app, event, facilitator):
    orphans = []
    with app.app_context():
        cd = app.config["CONTENT_DIR"]
        for slug in ("digital-footprint", "fake-infrastructure",
                     "threat-intelligence"):
            for entry in content.get_mission(cd, slug).get("artifacts", []):
                accounted = (entry.get("external_url")
                             or entry.get("run_it_yourself"))
                if not accounted:
                    orphans.append((slug, entry["id"]))
    assert not orphans, (
        "these sources print their contents with no reason recorded: %s"
        % orphans)
