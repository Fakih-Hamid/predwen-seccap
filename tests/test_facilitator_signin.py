import pathlib
import re

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

PASSWORD = "the-facilitator-password"
ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def console(app):
    """The real sign-in path, with a hash whose plaintext this file knows."""
    app.config["FACILITATOR_PASSWORD_HASH"] = generate_password_hash(PASSWORD)
    return app.test_client()


def token(client, url="/facilitator/login"):
    html = client.get(url).get_data(as_text=True)
    found = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert found, "the sign-in form carries no CSRF token"
    return found.group(1)


def sign_in(client, password=PASSWORD):
    return client.post("/facilitator/login",
                       data={"password": password,
                             "csrf_token": token(client)})


def test_sign_in_sign_out_sign_in_again(console):
    """Three full cycles. This is the thing that was reported as broken."""
    for _ in range(3):
        assert sign_in(console).status_code == 302
        assert console.get("/facilitator/").status_code == 200

        out = console.post("/facilitator/logout",
                           data={"csrf_token": token(console, "/facilitator/")})
        assert out.status_code == 302
        after = console.get("/facilitator/")
        assert after.status_code == 302
        assert "/facilitator/login" in after.headers["Location"]


def test_the_wrong_password_says_so_and_does_not_sign_anybody_in(console):
    r = sign_in(console, "not the password")
    assert r.status_code == 401
    assert console.get("/facilitator/").status_code == 302


def test_an_empty_password_is_refused(console):
    assert sign_in(console, "").status_code == 401


def test_no_password_configured_is_a_refusal_not_an_open_door(app):
    """An unset hash must never read as "no password needed"."""
    app.config["FACILITATOR_PASSWORD_HASH"] = ""
    client = app.test_client()
    assert client.post("/facilitator/login",
                       data={"password": "", "csrf_token": token(client)}
                       ).status_code == 401
    assert client.get("/facilitator/").status_code == 302


def test_a_stale_form_post_gets_a_page_and_not_a_json_blob(console):
    """What the operator actually saw at the worst possible moment."""
    r = console.post("/facilitator/login", data={"password": PASSWORD},
                     headers={"Accept": "text/html"})
    body = r.get_data(as_text=True)

    assert r.status_code == 400
    assert not body.lstrip().startswith("{")
    assert '"error":"csrf"' not in body.replace(" ", "")
    assert r.mimetype == "text/html"
    assert "<html" in body
    assert "Reload the previous page" in body or "再読み込み" in body


def test_the_fetch_writes_still_get_json(app, event, facilitator):
    """The participant screens write through fetch, and JSON is right there."""
    from .conftest import join

    p = join(app, event, "Team 1", "kenji")
    r = p.client.post("/api/observation",
                      json={"mission_slug": "digital-footprint", "text": "x"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "csrf"


def test_the_reset_script_exists_and_refuses_to_leak_the_plaintext():
    source = (ROOT / "scripts" / "set_facilitator_password.py").read_text(
        encoding="utf-8")
    assert "getpass.getpass" in source
    assert "add_argument(\"password\"" not in source
    assert "print(first)" not in source


def test_the_reset_script_rewrites_the_hash_in_place(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "setpw", ROOT / "scripts" / "set_facilitator_password.py")
    setpw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setpw)

    env = tmp_path / ".env"
    env.write_text(
        "SECCAP_SECRET_KEY=abc\n"
        "SECCAP_FACILITATOR_PASSWORD_HASH=scrypt:32768:8:1$old$0000\n"
        "SECCAP_DEFAULT_LANG=ja\n"
        "\n"
        "# a comment somebody put there on purpose\n"
        "PORT=8000\n", encoding="utf-8", newline="")

    digest = generate_password_hash("a-brand-new-password")
    assert setpw.rewrite(env, digest) == "replaced"

    written = env.read_text(encoding="utf-8")
    assert f"SECCAP_FACILITATOR_PASSWORD_HASH={digest}" in written
    assert "$old$" not in written
    for kept in ("SECCAP_SECRET_KEY=abc", "SECCAP_DEFAULT_LANG=ja",
                 "# a comment somebody put there on purpose", "PORT=8000"):
        assert kept in written, kept
    assert written.count("SECCAP_FACILITATOR_PASSWORD_HASH=") == 1


def test_the_reset_script_appends_when_the_key_is_absent(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "setpw", ROOT / "scripts" / "set_facilitator_password.py")
    setpw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setpw)

    env = tmp_path / ".env"
    env.write_text("SECCAP_SECRET_KEY=abc\n", encoding="utf-8", newline="")
    digest = generate_password_hash("another-password")
    assert setpw.rewrite(env, digest) == "appended"
    assert f"SECCAP_FACILITATOR_PASSWORD_HASH={digest}" in env.read_text(
        encoding="utf-8")


def test_a_hash_written_by_the_script_is_one_the_app_accepts(tmp_path):
    """The whole point: what it writes has to let somebody back in."""
    digest = generate_password_hash("round-trip-password")
    assert check_password_hash(digest, "round-trip-password")
    assert not check_password_hash(digest, "round-trip-passwore")


def setpw():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "setpw", ROOT / "scripts" / "set_facilitator_password.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def env_with(tmp_path, digest):
    env = tmp_path / ".env"
    env.write_text(f"SECCAP_SECRET_KEY=abc\n"
                   f"SECCAP_FACILITATOR_PASSWORD_HASH={digest}\n",
                   encoding="utf-8", newline="")
    return env


def test_verify_reads_the_same_hash_the_app_would(tmp_path):
    digest = generate_password_hash("known")
    assert setpw().current_hash(env_with(tmp_path, digest)) == digest


def test_verify_says_yes_to_the_right_password(tmp_path, monkeypatch):
    module = setpw()
    monkeypatch.setattr(module, "require_a_terminal", lambda: None)
    monkeypatch.setattr(module.getpass, "getpass", lambda _p: "known")
    assert module.verify(env_with(tmp_path, generate_password_hash("known"))) == 0


def test_verify_says_no_to_the_wrong_one(tmp_path, monkeypatch):
    module = setpw()
    monkeypatch.setattr(module, "require_a_terminal", lambda: None)
    monkeypatch.setattr(module.getpass, "getpass", lambda _p: "not it")
    assert module.verify(env_with(tmp_path, generate_password_hash("known"))) == 1


def test_verify_names_the_real_problem_when_the_hash_is_absent(tmp_path, capsys):
    module = setpw()
    env = tmp_path / ".env"
    env.write_text("SECCAP_SECRET_KEY=abc\n", encoding="utf-8", newline="")

    assert module.verify(env) == 1
    assert "disabled entirely" in capsys.readouterr().out


def test_it_refuses_without_a_terminal_instead_of_hanging(monkeypatch):
    import io

    module = setpw()
    monkeypatch.setattr(module.sys, "stdin", io.StringIO())   # not a tty

    with pytest.raises(SystemExit) as raised:
        module.require_a_terminal()
    assert "needs a real terminal" in str(raised.value)
    assert "set_facilitator_password.py" in str(raised.value)


def test_writing_verifies_the_hash_survived_the_round_trip(tmp_path):
    module = setpw()
    env = tmp_path / ".env"
    env.write_text("SECCAP_SECRET_KEY=abc\n", encoding="utf-8", newline="")

    digest = generate_password_hash("has$dollars$in$it")
    module.rewrite(env, digest)
    assert module.current_hash(env) == digest
    assert check_password_hash(module.current_hash(env), "has$dollars$in$it")


@pytest.mark.parametrize("typed,hashed,expect", [
    ("summer$word-2026", "summer-2026", "expanded a $variable"),
    ("summer${word}2026", "summer2026", "${...} reference"),
    ("sum`mer2026", "summer2026", "backtick"),
    ("sum\\mer2026", "summer2026", "backslash"),
    ("summer%PATH%2026", "summer2026", "%VARIABLE%"),
    ("summer2026 ", "summer2026", "leading or trailing space"),
])
def test_it_names_the_shell_that_ate_the_password(typed, hashed, expect):
    module = setpw()
    digest = generate_password_hash(hashed)

    assert not check_password_hash(digest, typed), "the premise of this test"
    cause = module.diagnose(digest, typed)
    assert cause is not None, typed
    assert expect in cause, (typed, cause)


def test_it_says_nothing_when_the_password_is_simply_wrong():
    module = setpw()
    digest = generate_password_hash("the-real-one")

    assert module.diagnose(digest, "a-completely-different-guess") is None
    assert module.diagnose(digest, "something-else$x") is None


def test_the_diagnosis_never_contains_the_password():
    module = setpw()
    digest = generate_password_hash("summer-2026")
    cause = module.diagnose(digest, "summer$word-2026")

    assert cause
    for secret in ("summer-2026", "summer$word-2026", "summer"):
        assert secret not in cause, secret


def test_verify_prints_the_diagnosis_rather_than_just_refusing(tmp_path,
                                                               monkeypatch,
                                                               capsys):
    module = setpw()
    env = env_with(tmp_path, generate_password_hash("summer-2026"))
    monkeypatch.setattr(module, "require_a_terminal", lambda: None)
    monkeypatch.setattr(module.getpass, "getpass", lambda _p: "summer$word-2026")

    assert module.verify(env) == 1
    out = capsys.readouterr().out
    assert "BUT IT IS THE PASSWORD YOU MEANT" in out
    assert "expanded a $variable" in out
    assert "summer" not in out, "the diagnosis leaked the password"


def test_verify_does_not_rewrite_the_file(tmp_path, monkeypatch):
    module = setpw()
    env = env_with(tmp_path, generate_password_hash("known"))
    before = env.read_bytes()
    monkeypatch.setattr(module, "require_a_terminal", lambda: None)
    monkeypatch.setattr(module.getpass, "getpass", lambda _p: "not it")

    module.verify(env)
    assert env.read_bytes() == before
