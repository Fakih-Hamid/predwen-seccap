import os
import re
import shutil
import subprocess
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY = os.path.join(ROOT, "deploy")


def read(*parts):
    with open(os.path.join(DEPLOY, *parts), encoding="utf-8") as fh:
        return fh.read()


def code_only(text):
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load(read("compose.prod.yml"))


def test_the_application_publishes_no_host_port(compose):
    """The one that matters. Only Caddy is on the internet."""
    app = compose["services"]["seccap"]
    assert "ports" not in app, (
        "seccap publishes a host port; it would bypass Caddy and Basic Auth")
    assert "8000" in [str(p) for p in app["expose"]]


def test_only_caddy_publishes_and_only_ssh_http_https_are_open(compose):
    published = {str(p).split(":")[0] for p in compose["services"]["caddy"]["ports"]}
    assert published == {"80", "443"}, published

    firewall = read("firewall.sh")
    allowed = set(re.findall(r"^ufw (?:allow|limit) (\S+)",
                             firewall.replace('"', ""), re.M))
    assert allowed == {"$SSH_PORT/tcp", "80/tcp", "443/tcp", "443/udp"}, allowed
    assert "ufw default deny incoming" in firewall


def test_both_images_are_pinned_to_a_digest(compose):
    caddy = compose["services"]["caddy"]["image"]
    assert "@sha256:" in caddy, caddy
    dockerfile = open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read()
    assert "@sha256:" in dockerfile


def test_four_workers_and_exactly_one_trusted_proxy(compose):
    env = compose["services"]["seccap"]["environment"]
    assert env["SECCAP_WORKERS"] == "4"
    assert env["SECCAP_TRUSTED_PROXIES"] == "1"
    assert env["SECCAP_COOKIE_SECURE"] == "1"
    assert env["SECCAP_ADMIN_BYPASS"] == ""


def test_both_password_hashes_are_passed_to_the_container(compose):
    raw = read("compose.prod.yml")
    env = compose["services"]["seccap"]["environment"]
    assert "SECCAP_FACILITATOR_PASSWORD_HASH" in env
    assert "SECCAP_ASSISTANT_PASSWORD_HASH" in env, (
        "the TA password never reaches the app; /assistant would refuse everyone")
    assert "${SECCAP_FACILITATOR_PASSWORD_HASH:?" in raw
    assert "${SECCAP_ASSISTANT_PASSWORD_HASH:-}" in raw
    assert "${SECCAP_ASSISTANT_PASSWORD_HASH:?" not in raw, (
        "the TA hash must be optional: a session with no TAs must still deploy")


def test_the_only_thing_mounted_into_the_application_is_the_database(compose):
    app = compose["services"]["seccap"]
    mounts = app["volumes"]
    assert mounts == ["seccap-data:/data"], mounts
    assert "seccap-data" in compose["volumes"]


def test_the_image_is_not_built_by_compose_and_is_never_pulled(compose):
    """deploy.sh builds it from a worktree at the SHA; compose only runs it."""
    app = compose["services"]["seccap"]
    assert "build" not in app, (
        "a build: here would build the checkout, not the commit being deployed")
    assert app["pull_policy"] == "never", (
        "a missing image is a deployment that never ran, not a tag to fetch")
    assert app["image"].startswith("predwen-seccap:")


def test_both_services_rotate_their_logs(compose):
    """A small VPS disk is the same disk SQLite writes to."""
    for name in ("seccap", "caddy"):
        logging = compose["services"][name]["logging"]
        assert logging["driver"] == "json-file", name
        assert logging["options"]["max-size"] == "10m", name
        assert int(logging["options"]["max-file"]) >= 2, name


def test_caddy_keeps_its_certificates(compose):
    targets = {m.split(":")[1] for m in compose["services"]["caddy"]["volumes"]}
    assert {"/data", "/config"} <= targets, targets


def test_the_site_address_and_credentials_come_from_variables():
    caddyfile = read("Caddyfile")
    assert "{$SECCAP_SITE_ADDRESS}" in caddyfile
    assert "sakura-vpn-update" not in caddyfile
    assert not re.search(r"^\s*[a-z0-9-]+\.[a-z]{2,}\s*\{", caddyfile, re.M)


def test_an_empty_acme_email_cannot_take_the_site_down():
    """An `email` directive with an unset variable is a startup error."""
    assert "email {$" not in read("Caddyfile")


def test_hsts_is_set_explicitly_and_conservatively():
    """Caddy does not add HSTS on its own — it only redirects HTTP to HTTPS."""
    caddyfile = read("Caddyfile")
    assert re.search(
        r'^\s*header Strict-Transport-Security "max-age=31536000"\s*$',
        caddyfile, re.M), "no explicit HSTS header"
    assert "includeSubDomains" not in code_only(caddyfile)
    assert "preload" not in code_only(caddyfile)
    assert "Caddy adds HSTS on its own" not in caddyfile


def test_the_committed_template_holds_no_values():
    template = read("env.production.example")
    for key in ("SECCAP_SECRET_KEY", "SECCAP_BASIC_AUTH_HASH",
                "SECCAP_FACILITATOR_PASSWORD_HASH"):
        assert re.search(r"^%s=''$" % key, template, re.M), key
    assert "sakura-vpn-update" not in code_only(template)
    assert re.search(r"^SECCAP_SITE_ADDRESS=seccap\.example\.org$", template, re.M)


def test_a_dollar_sign_in_a_hash_cannot_be_eaten_by_interpolation():
    template = read("env.production.example")
    assert "KEEP THE SINGLE QUOTES" in template
    lib = read("_lib.sh")
    assert "set -a" in lib and '. "$ENV_FILE"' in lib
    assert "does not look like a Werkzeug hash" in read("deploy.sh")


def test_the_real_environment_file_would_be_ignored():
    ignore = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
    assert ".env.*" in ignore and "backups/" in ignore
    assert not os.path.exists(os.path.join(DEPLOY, ".env.production.example"))
    assert os.path.exists(os.path.join(DEPLOY, "env.production.example"))


SCRIPTS = ("deploy.sh", "backup.sh", "restore.sh", "rollback.sh",
           "verify.sh", "firewall.sh", "generate-secrets.sh", "_lib.sh")


@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_are_posix_sh_with_unix_line_endings(name):
    raw = open(os.path.join(DEPLOY, name), "rb").read()
    assert b"\r\n" not in raw, f"{name} has CRLF; dash would refuse the shebang"
    if name != "_lib.sh":                      # sourced, so it has no shebang
        assert raw.startswith(b"#!/bin/sh"), name


@pytest.mark.parametrize("name", SCRIPTS)
def test_no_script_can_delete_the_data_volume(name):
    """`down -v` is the one flag that loses the event."""
    text = code_only(read(name))
    assert not re.search(r"down\s+(-\w+\s+)*-v\b", text), name
    assert not re.search(r"down\s+.*--volumes", text), name
    assert "volume rm" not in text, name
    assert "volume prune" not in text, name


@pytest.mark.parametrize("name", SCRIPTS)
def test_no_script_resets_the_repository(name):
    """Not `git reset`, hard or otherwise, and not `git clean`."""
    text = code_only(read(name))
    assert "git reset" not in text, name
    assert "git clean" not in text, name
    assert not re.search(r"checkout\s+(--detach|-f|--force)", text), name


def test_the_deploy_script_verifies_the_expected_sha():
    text = read("deploy.sh")
    assert 'EXPECTED=${1:-}' in text
    assert "git rev-parse HEAD" in text
    assert "refusing to deploy" in text
    assert "git status --porcelain" in text        # and a clean tree
    assert "sakura-vpn-update" in text             # the guard, not a value


def test_the_backup_never_copies_the_database_file_alone():
    """WAL means `cp seccap.db` silently loses the end of the event."""
    text = read("backup.sh")
    assert "scripts/backup_db.py" in text
    assert not re.search(r"\bcp\b[^\n]*seccap\.db", code_only(text))
    assert "--offline" in text                     # controlled stop, if wanted

    api = open(os.path.join(ROOT, "scripts", "backup_db.py"), encoding="utf-8").read()
    assert "src.backup(dst)" in api, "the SQLite backup API is the mechanism"
    assert "integrity_check" in api                # and the copy is verified


def test_the_backup_program_comes_from_the_checkout_not_the_image():
    text = code_only(read("backup.sh"))
    assert 'BACKUP_PROGRAM="$REPO_ROOT/scripts/backup_db.py"' in text
    assert 'python - "$INNER" < "$BACKUP_PROGRAM"' in text
    assert "python scripts/backup_db.py" not in text
    assert "--entrypoint python" in text            # the offline path, likewise


def test_the_restore_refuses_an_active_database_without_confirmation():
    text = read("restore.sh")
    assert "SECCAP_RESTORE_CONFIRM" in text
    assert "OVERWRITE" in text
    assert "not confirmed" in text
    assert "mv seccap.db-wal seccap.db-wal.prev" in text
    assert "rm -f seccap.db seccap.db-wal seccap.db-shm" in text
    assert "backup.sh" in text


def test_the_restore_is_failure_safe_once_the_application_is_stopped():
    text = read("restore.sh")
    assert "trap on_exit EXIT INT TERM HUP" in text
    assert "APP_WAS_STOPPED" in text
    assert "RESTORE_DONE" in text
    assert "mv seccap.db     seccap.db.prev" in text
    assert "mv seccap.db.prev seccap.db" in text          # the way back
    assert text.index("wait_for_healthz 120") < text.index('on_volume "$COMMIT"')
    assert "compose up -d >/dev/null 2>&1 || true" in text
    assert "SECCAP_RESTORE_INJECT_FAILURE" in text
    assert "after-stop" in text and "after-swap" in text


def test_the_swap_never_swallows_a_failing_move():
    """`[ -f x ] && mv ... || true` hides a failed mv as well as a missing file."""
    text = code_only(read("restore.sh"))     # the comment quotes the bad form
    assert not re.search(r"&&\s*mv\b.*\|\|\s*true", text)
    assert "if [ -f seccap.db ];     then mv" in text


def test_the_rollback_builds_a_worktree_and_never_moves_the_checkout():
    """500691a has no deploy/ — checking it out would delete these scripts."""
    text = read("rollback.sh")
    assert "build_image_at" in text
    assert "git checkout" not in code_only(text)
    assert "checkout_state" in text and "the checkout moved" in text
    assert "VOL_CREATED" in text and "the volume was recreated" in text
    assert "teams went from" in text

    lib = read("_lib.sh")
    assert "worktree add --detach" in lib
    assert "worktree remove --force" in lib      # cleanup, including on failure
    assert "trap cleanup_build_tree EXIT INT TERM HUP" in read("deploy.sh")
    assert "trap cleanup_build_tree EXIT INT TERM HUP" in text


def test_backup_names_are_unique_within_the_same_second():
    """A restore takes a backup, and so does the deploy that called it."""
    text = read("backup.sh")
    assert 'DEST="$DEST_DIR/seccap-$STAMP-$n.db"' in text
    assert "n=$((n + 1))" in text


def test_the_environment_file_is_validated_strictly_before_it_is_sourced():
    lib = read("_lib.sh")
    assert "validate_env.py" in lib
    assert "EXECUTED AS SHELL" in read("env.production.example")
    assert lib.index("validate_env.py") < lib.index('. "$ENV_FILE"')
    assert 'sh -n "$ENV_FILE"' not in lib


def test_the_validator_never_evaluates_what_it_reads():
    """It is a text matcher. Nothing in it can run the file's content."""
    source = code_only(read("validate_env.py"))
    for forbidden in ("eval(", "exec(", "subprocess", "os.system", "popen",
                      "importlib", "__import__"):
        assert forbidden not in source, forbidden
    assert "re.compile" in source
    assert 'open(path, "r", encoding="utf-8"' in source


HOSTILE = {
    "a bare command": "touch {marker}\n",
    "a command substitution": "SECCAP_SECRET_KEY=$(touch {marker})\n",
    "backticks": "SECCAP_SECRET_KEY=`touch {marker}`\n",
    "an unknown key": "SECCAP_EVIL='x'\n",
    "a duplicated key": (
        "SECCAP_SECRET_KEY='"
        + "a" * 40 + "'\nSECCAP_SECRET_KEY='" + "b" * 40 + "'\n"),
    "a trailing command": "SECCAP_SECRET_KEY='" + "a" * 40 + "'; touch {marker}\n",
}


def valid_env_text():
    """What generate-secrets.sh writes, in the shape validate_env.py accepts."""
    return (
        "# Generated by deploy/generate-secrets.sh. Do not commit.\n"
        "\n"
        "SECCAP_SITE_ADDRESS='seccap.example.org'\n"
        "SECCAP_BASIC_AUTH_USER='seccap'\n"
        "SECCAP_BASIC_AUTH_HASH='$2a$14$"
        + "a" * 53 + "'\n"
        "SECCAP_SECRET_KEY='" + "K" * 64 + "'\n"
        "SECCAP_FACILITATOR_PASSWORD_HASH="
        "'scrypt:32768:8:1$abcDEF0123456789$" + "0" * 128 + "'\n"
        "SECCAP_DEFAULT_LANG='ja'\n"
        "SECCAP_JOIN_RPM='60'\n"
        "SECCAP_IMAGE_TAG='rc'\n")


def write_env(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def run_validator(path):
    return subprocess.run(
        [sys.executable, os.path.join(DEPLOY, "validate_env.py"), str(path)],
        capture_output=True, text=True)


@pytest.mark.parametrize("label", sorted(HOSTILE))
def test_a_hostile_environment_file_is_refused_and_never_runs(label, tmp_path):
    marker = tmp_path / "pwned"
    env = tmp_path / ".env.production"
    write_env(env, HOSTILE[label].format(marker=str(marker).replace("\\", "/")))

    done = run_validator(env)
    assert done.returncode != 0, (label, done.stdout)
    assert "REFUSING" in done.stderr, (label, done.stderr)
    assert not marker.exists(), f"{label}: the file was EXECUTED"


def test_the_hostile_files_really_are_valid_shell():
    sh = shutil.which("sh") or shutil.which("bash")
    if sh is None:
        pytest.skip("no POSIX shell on PATH")
    for label in ("a bare command", "a command substitution", "backticks",
                  "a trailing command"):
        text = HOSTILE[label].format(marker="/tmp/never-created-by-a-parse")
        done = subprocess.run([sh, "-n"], input=text, capture_output=True,
                              text=True)
        assert done.returncode == 0, (label, done.stderr)


def test_a_well_formed_file_is_accepted(tmp_path):
    env = tmp_path / ".env.production"
    write_env(env, valid_env_text())
    done = run_validator(env)
    assert done.returncode == 0, done.stderr
    assert "is inert" in done.stdout


def test_a_missing_required_key_is_refused(tmp_path):
    env = tmp_path / ".env.production"
    write_env(env, valid_env_text().replace(
        "SECCAP_FACILITATOR_PASSWORD_HASH=", "# removed="))
    done = run_validator(env)
    assert done.returncode != 0
    assert "SECCAP_FACILITATOR_PASSWORD_HASH is missing" in done.stderr


def test_a_standing_restore_confirmation_is_refused(tmp_path):
    """A permanent yes to "overwrite the live database" is not configuration."""
    env = tmp_path / ".env.production"
    write_env(env, valid_env_text() + "SECCAP_RESTORE_CONFIRM='OVERWRITE'\n")
    done = run_validator(env)
    assert done.returncode != 0
    assert "does not belong in this file" in done.stderr


def test_crlf_is_refused_because_it_would_corrupt_every_hash(tmp_path):
    env = tmp_path / ".env.production"
    env.write_bytes(valid_env_text().replace("\n", "\r\n").encode("utf-8"))
    done = run_validator(env)
    assert done.returncode != 0
    assert "CR" in done.stderr


def test_the_accepted_file_round_trips_every_secret_byte_for_byte(tmp_path):
    sh = shutil.which("sh") or shutil.which("bash")
    if sh is None:
        pytest.skip("no POSIX shell on PATH")

    env = tmp_path / ".env.production"
    write_env(env, valid_env_text())
    assert run_validator(env).returncode == 0

    posix = str(env).replace("\\", "/")
    for key in ("SECCAP_BASIC_AUTH_HASH", "SECCAP_SECRET_KEY",
                "SECCAP_FACILITATOR_PASSWORD_HASH"):
        done = subprocess.run(
            [sh, "-c", 'set -a; . "$1"; set +a; printf "%%s" "$%s"' % key,
             "sh", posix],
            capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        expected = re.search(r"^%s='([^']*)'$" % key, valid_env_text(),
                             re.M).group(1)
        assert done.stdout == expected, (key, done.stdout, expected)
        assert "$" in expected or key == "SECCAP_SECRET_KEY"


def test_the_secret_generator_keeps_passwords_off_the_command_line():
    text = read("generate-secrets.sh")
    assert "stty -echo" in text
    assert 'printf \'%s\' "$FAC_PW" | docker run --rm -i' in text
    assert 'printf \'%s\\n\' "$BASIC_PW" | docker run --rm -i' in text
    assert "--plaintext" not in code_only(text)
    assert "hash-password" in text
    assert "read_confirmed" in text
    assert 'chmod 600 "$ENV_FILE"' in text
    assert "did not survive being written" in text
    assert "python:3.12.14-slim@sha256:" in text
    assert "caddy:2.11.4-alpine@sha256:" in text


def test_the_generated_facilitator_hash_is_one_werkzeug_accepts():
    import hashlib
    import secrets
    import string

    from werkzeug.security import check_password_hash

    password = "a facilitator password with $dollars"
    alphabet = string.ascii_letters + string.digits
    salt = "".join(secrets.choice(alphabet) for _ in range(16))
    n, r, p = 32768, 8, 1
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=n, r=r,
                            p=p, maxmem=132 * n * r * p, dklen=64).hex()
    generated = "scrypt:%d:%d:%d$%s$%s" % (n, r, p, salt, digest)

    assert check_password_hash(generated, password)
    assert not check_password_hash(generated, password + "x")

    text = read("generate-secrets.sh")
    assert "maxmem=132 * n * r * p" in text
    assert "dklen=64" in text
    assert "n, r, p = 32768, 8, 1" in text


def test_verify_checks_disk_space_and_healthz():
    text = read("verify.sh")
    assert "df -P /" in text
    assert "/healthz" in text
    assert "401" in text                # unauthenticated must be refused
    assert "127.0.0.1:8000" in text     # and the app must not be exposed


SSHD_EXPECTED = {
    "passwordauthentication": "no",
    "kbdinteractiveauthentication": "no",
    "pubkeyauthentication": "yes",
    "permitrootlogin": "no",
    "allowusers": "seccap",
}


def find_sshd():
    for candidate in ("sshd", "/usr/sbin/sshd", "/sbin/sshd"):
        found = shutil.which(candidate) if os.sep not in candidate else (
            candidate if os.path.exists(candidate) else None)
        if found:
            return found
    return None


def sshd_effective_config(tmp_path, dropin_name):
    sshd = find_sshd()
    keygen = shutil.which("ssh-keygen")
    if sshd is None or keygen is None:
        if os.environ.get("SECCAP_REQUIRE_SSHD"):
            raise AssertionError(
                "SECCAP_REQUIRE_SSHD is set but sshd/ssh-keygen were not found")
        pytest.skip("no sshd on this machine")

    confd = tmp_path / "sshd_config.d"
    confd.mkdir()
    write_env(confd / dropin_name, read("sshd_hardening.conf"))
    write_env(confd / "50-cloud-init.conf",
              "# Created by cloud-init\nPasswordAuthentication yes\n")

    hostkey = tmp_path / "host_ed25519"
    subprocess.run([keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(hostkey)],
                   check=True, capture_output=True)

    config = tmp_path / "sshd_config"
    write_env(config, "Include %s/*.conf\nHostKey %s\n"
              % (confd.as_posix(), hostkey.as_posix()))

    done = subprocess.run([sshd, "-T", "-f", str(config)],
                          capture_output=True, text=True)
    assert done.returncode == 0, (
        "sshd -T failed:\n%s\n%s" % (done.stdout, done.stderr))

    parsed = {}
    for line in done.stdout.splitlines():
        if " " in line:
            key, _, value = line.partition(" ")
            parsed.setdefault(key.lower(), value.strip())
        else:
            parsed.setdefault(line.lower(), "")
    return parsed


def test_the_sshd_dropin_wins_over_cloud_init(tmp_path):
    """THE REGRESSION TEST. Not syntax — the effective configuration."""
    effective = sshd_effective_config(tmp_path, "00-predwen.conf")
    for key, expected in SSHD_EXPECTED.items():
        assert effective.get(key) == expected, (
            "%s is %r, expected %r" % (key, effective.get(key), expected))


def test_the_sshd_dropin_named_99_is_the_bug_that_was_found(tmp_path):
    effective = sshd_effective_config(tmp_path, "99-predwen.conf")
    assert effective.get("passwordauthentication") == "yes", (
        "cloud-init no longer sets PasswordAuthentication yes, so this test "
        "no longer demonstrates anything")
    assert effective.get("permitrootlogin") == "no"
    assert effective.get("pubkeyauthentication") == "yes"
    assert effective.get("allowusers") == "seccap"
