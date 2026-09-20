import os
import subprocess

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGINAL = os.path.join(os.path.dirname(HERE), "PREDWEN V3 DUAL DESIGN IN ONE")
BASELINE = os.path.join(HERE, ".baseline-original-HEAD.txt")


def _git(*args):
    return subprocess.run(["git", "-C", ORIGINAL] + list(args),
                          capture_output=True, text=True, timeout=60)


@pytest.mark.skipif(not os.path.isdir(os.path.join(ORIGINAL, ".git")),
                    reason="the original Predwen checkout is not beside this one")
def test_the_original_repository_is_unchanged():
    with open(BASELINE, encoding="utf-8") as f:
        lines = [line.rstrip("\n") for line in f if line.strip()]
    baseline_head = lines[0]
    baseline_status = lines[1:]

    head = _git("rev-parse", "HEAD")
    assert head.returncode == 0, head.stderr
    assert head.stdout.strip() == baseline_head, (
        "the original repository's HEAD moved: it must be treated as read-only")

    status = _git("status", "--porcelain")
    assert status.returncode == 0, status.stderr
    now = [line for line in status.stdout.splitlines() if line.strip()]
    assert now == baseline_status, (
        "the original repository's working tree changed:\n" + "\n".join(now))


@pytest.mark.skipif(not os.path.isdir(os.path.join(ORIGINAL, ".git")),
                    reason="the original Predwen checkout is not beside this one")
def test_this_repository_has_its_own_history_and_not_the_originals_remote():
    ours = subprocess.run(["git", "-C", HERE, "remote", "-v"],
                          capture_output=True, text=True, timeout=60)
    theirs = _git("remote", "get-url", "origin")
    if theirs.returncode == 0 and theirs.stdout.strip():
        assert theirs.stdout.strip() not in ours.stdout, (
            "this repository must not push to the research platform's remote")
