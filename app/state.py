import secrets

from .models import (
    MISSION_CLOSED,
    MISSION_LOCKED,
    MISSION_OPEN,
    MISSION_PAUSED,
    SESSION_CLOSED,
    SESSION_RUNNING,
    SESSION_SETUP,
    UNLOCK_AUTOMATIC,
    UNLOCK_FACILITATOR,
    UNLOCK_STUDENT,
    EventSession,
    HintUnlock,
    Mission,
    Team,
    TeamArtifact,
    TeamMission,
    aware,
    audit,
    db,
    utcnow,
)
from . import missions as content

TEAM_PRESETS = (
    ("Team 1", "teal"),
    ("Team 2", "amber"),
    ("Team 3", "violet"),
    ("Team 4", "green"),
    ("Team 5", "rose"),
    ("Team 6", "sky"),
)

DEFAULT_TEAM_COUNT = 6

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

TEAM_CODE_LENGTH = 5
MAX_TEAM_NAME = 40
MIN_TEAM_NAME = 2

FIXED_TEAM_CODES = ("TCACW", "VX55N", "Z6ATK", "P9FX6", "SD38L", "K7RMH")

TEAM_NAMES = ["Team Kitsune", "Team Raijin", "Team Tanuki", "Team Koi",
              "Team Tsubasa", "Team Tengu"]
TEAM_CAPACITIES = [4, 4, 4, 5, 5, 5]


def apply_table_plan(ev, names=None, capacities=None, commit=True):
    names = TEAM_NAMES if names is None else names
    capacities = TEAM_CAPACITIES if capacities is None else capacities
    teams = Team.query.filter_by(session_id=ev.id).order_by(Team.id).all()
    for team, name in zip(teams, names):
        team.display_name = name[:MAX_TEAM_NAME]
    for team, capacity in zip(teams, capacities):
        team.capacity = capacity
    if commit:
        db.session.commit()
    return teams


def new_code(length=6):
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def _fresh_team_code(taken):
    code = new_code(TEAM_CODE_LENGTH)
    while code in taken:
        code = new_code(TEAM_CODE_LENGTH)
    return code


def normalise_team_name(raw):
    """(name, error) — the error key is a ui.yaml suffix under `team_name.`."""
    import re
    import unicodedata

    name = unicodedata.normalize("NFKC", str(raw or ""))
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) < MIN_TEAM_NAME:
        return None, "too_short"
    if len(name) > MAX_TEAM_NAME:
        return None, "too_long"
    return name, None


def names_locked(ev):
    return any(row.opened_at is not None for row in mission_rows(ev))


def rename_team(ev, team, raw):
    if names_locked(ev):
        return False, "locked"
    name, error = normalise_team_name(raw)
    if error:
        return False, error
    clash = [t for t in Team.query.filter_by(session_id=ev.id).all()
             if t.id != team.id and t.display_name.casefold() == name.casefold()]
    if clash:
        return False, "taken"
    old = team.display_name
    team.display_name = name
    audit(ev.id, "member", None, "team_renamed", team.code, f"{old} → {name}")
    db.session.commit()
    return True, None


def add_team(ev, index=None, taken=None, commit=True, actor="facilitator"):
    if index is None:
        index = Team.query.filter_by(session_id=ev.id).count()
    if index < len(TEAM_PRESETS):
        name, color = TEAM_PRESETS[index]
    else:
        name, color = f"Team {index + 1}", ""

    if taken is None:
        taken = {t.code for t in Team.query.filter_by(session_id=ev.id).all()}
    if index < len(FIXED_TEAM_CODES) and FIXED_TEAM_CODES[index] not in taken:
        code = FIXED_TEAM_CODES[index]
    else:
        code = _fresh_team_code(taken)
    taken.add(code)
    team = Team(session_id=ev.id, code=code, display_name=name, color_key=color)
    db.session.add(team)
    if commit:
        db.session.flush()
        audit(ev.id, "facilitator", actor, "team_added", code, name)
        db.session.commit()
    return team


def create_session(config, title=None, code=None, team_count=DEFAULT_TEAM_COUNT,
                   actor="facilitator"):
    code = (code or new_code()).upper()
    while EventSession.query.filter_by(code=code).first() is not None:
        code = new_code()

    ev = EventSession(code=code, title=title or "SECCAP Incident Exercise",
                      state=SESSION_SETUP)
    db.session.add(ev)
    db.session.flush()

    taken = set()
    for i in range(team_count):
        add_team(ev, index=i, taken=taken, commit=False)

    for m in content.ordered_missions(config["CONTENT_DIR"]):
        db.session.add(Mission(session_id=ev.id, slug=m["slug"], order=m.get("order", 0),
                               duration_seconds=int(m.get("duration_seconds", 1800)),
                               content_version=str(m.get("version")),
                               state=MISSION_LOCKED))

    audit(ev.id, "facilitator", actor, "session_created", ev.code,
          f"{team_count} teams")
    db.session.commit()
    return ev


def active_session():
    return (EventSession.query.filter(EventSession.state != SESSION_CLOSED)
            .order_by(EventSession.created_at.desc(), EventSession.id.desc()).first())


def session_by_code(code):
    if not code:
        return None
    return EventSession.query.filter_by(code=(code or "").strip().upper()).first()


def team_by_code(ev, code):
    if ev is None or not code:
        return None
    return Team.query.filter_by(session_id=ev.id, code=(code or "").strip().upper()).first()


def mission_row(ev, slug):
    return Mission.query.filter_by(session_id=ev.id, slug=slug).one_or_none()


def mission_rows(ev):
    return Mission.query.filter_by(session_id=ev.id).order_by(Mission.order).all()


def open_mission(ev, row, actor="facilitator"):
    from .scoring import undo_auto_scoring

    now = utcnow()
    reopened = 0
    if row.opened_at is None:
        row.opened_at = now
    elif row.state == MISSION_CLOSED and row.closed_at is not None:
        reopened = undo_auto_scoring(ev, row, actor="server")
        row.paused_seconds += int((now - aware(row.closed_at)).total_seconds())
        row.closed_at = None
    elif row.state == MISSION_PAUSED and row.paused_at is not None:
        row.paused_seconds += int((now - aware(row.paused_at)).total_seconds())
    row.paused_at = None
    row.state = MISSION_OPEN
    ev.state = SESSION_RUNNING
    audit(ev.id, "facilitator", actor, "mission_open", row.slug,
          f"reopened; auto-scoring reversed for {reopened} team(s)"
          if reopened else None)
    db.session.commit()
    return row


def pause_mission(ev, row, actor="facilitator"):
    if row.state != MISSION_OPEN:
        return row
    row.paused_at = utcnow()
    row.state = MISSION_PAUSED
    audit(ev.id, "facilitator", actor, "mission_pause", row.slug,
          f"{row.remaining_seconds()}s left")
    db.session.commit()
    return row


def extend_mission(ev, row, seconds, actor="facilitator"):
    seconds = max(-3600, min(3600, int(seconds)))
    row.extension_seconds += seconds
    audit(ev.id, "facilitator", actor, "mission_extend", row.slug, f"{seconds:+d}s")
    db.session.commit()
    return row


def close_mission(ev, row, actor="facilitator"):
    from .scoring import score_mission

    first_close = row.state != MISSION_CLOSED
    if first_close:
        row.state = MISSION_CLOSED
        row.closed_at = utcnow()
        row.paused_at = None
        audit(ev.id, "facilitator", actor, "mission_close", row.slug)
        db.session.commit()

    definition = content.get_mission(_content_dir(), row.slug)
    if definition is None:
        return row

    for team in Team.query.filter_by(session_id=ev.id).all():
        score_mission(ev, team, row, definition, actor="server")
        tm = TeamMission.query.filter_by(team_id=team.id, mission_slug=row.slug).one_or_none()
        if tm is not None and tm.submitted_at is None:
            tm.submitted_at = row.closed_at
            db.session.commit()

    if not first_close:
        audit(ev.id, "facilitator", actor, "mission_rescore", row.slug,
              "close pressed on an already-closed mission")
        db.session.commit()
    return row


def _content_dir():
    from flask import current_app
    return current_app.config["CONTENT_DIR"]


def lock_submission(ev, team, row, member, actor=None):
    tm = TeamMission.query.filter_by(team_id=team.id, mission_slug=row.slug).one_or_none()
    if tm is None:
        tm = TeamMission(session_id=ev.id, team_id=team.id, mission_slug=row.slug)
        db.session.add(tm)
    if tm.submitted_at is None:
        tm.submitted_at = utcnow()
        tm.locked_by = member.id if member else None
    audit(ev.id, "member", actor or (member.id if member else None), "mission_lock",
          f"{team.code}/{row.slug}")
    db.session.commit()
    return tm


def unlock_submission(ev, team, row, actor="facilitator"):
    tm = TeamMission.query.filter_by(team_id=team.id, mission_slug=row.slug).one_or_none()
    if tm is None:
        return None
    tm.submitted_at = None
    tm.locked_by = None
    audit(ev.id, "facilitator", actor, "mission_unlock", f"{team.code}/{row.slug}")
    db.session.commit()
    return tm


def team_mission(ev, team, slug, create=True):
    tm = TeamMission.query.filter_by(team_id=team.id, mission_slug=slug).one_or_none()
    if tm is None and create:
        tm = TeamMission(session_id=ev.id, team_id=team.id, mission_slug=slug,
                         first_opened_at=utcnow())
        db.session.add(tm)
        db.session.commit()
    return tm


def is_locked(team, slug):
    tm = TeamMission.query.filter_by(team_id=team.id, mission_slug=slug).one_or_none()
    return tm is not None and tm.submitted_at is not None


def can_write(ev, team, row):
    if row is None or not row.accepts_writes():
        return False
    return not is_locked(team, row.slug)


def open_pivot(ev, team, row, artifact_id):
    existing = TeamArtifact.query.filter_by(team_id=team.id,
                                            artifact_id=artifact_id).one_or_none()
    if existing is not None:
        return existing
    stamp = TeamArtifact(session_id=ev.id, team_id=team.id,
                         mission_slug=row.slug if row is not None else "",
                         artifact_id=artifact_id,
                         opened_at_elapsed=row.elapsed_seconds() if row else 0)
    db.session.add(stamp)
    db.session.commit()
    return stamp


def pivot_elapsed(row, team, artifact_id):
    """Seconds this team has spent on one pivot, or None if it never opened it."""
    stamp = TeamArtifact.query.filter_by(team_id=team.id,
                                         artifact_id=artifact_id).one_or_none()
    if stamp is None or row is None:
        return None
    return max(0, row.elapsed_seconds() - stamp.opened_at_elapsed)


def hint_is_open(ev, row, team, hint):
    from . import missions as content

    after = content.hint_unlock_after(hint)
    if after <= 0:
        return True
    if HintUnlock.query.filter_by(team_id=team.id, hint_id=hint["id"]).first() is not None:
        return True
    if row is None:
        return False
    if row.state == MISSION_CLOSED or (row.opened_at is not None
                                       and row.remaining_seconds() <= 0):
        return True
    if row.state == MISSION_LOCKED:
        return False
    return _ladder_elapsed(row, team, hint) >= after


def _ladder_elapsed(row, team, hint):
    artifact_id = hint.get("artifact_id")
    if not artifact_id:
        return row.elapsed_seconds()
    elapsed = pivot_elapsed(row, team, artifact_id)
    return -1 if elapsed is None else elapsed


def hint_opens_in(ev, row, team, hint):
    """Seconds until this rung opens by itself, or 0 when it already has."""
    from . import missions as content

    if hint_is_open(ev, row, team, hint):
        return 0
    elapsed = _ladder_elapsed(row, team, hint)
    if elapsed < 0:                       # pivot never opened: no clock running
        return content.hint_unlock_after(hint)
    return max(0, content.hint_unlock_after(hint) - elapsed)


def unlock_hint(ev, team, row, hint, mode=UNLOCK_FACILITATOR, actor="facilitator"):
    hint_id = hint["id"] if isinstance(hint, dict) else hint
    existing = HintUnlock.query.filter_by(team_id=team.id, hint_id=hint_id).one_or_none()
    if existing is not None:
        return existing
    unlock = HintUnlock(session_id=ev.id, team_id=team.id,
                        mission_slug=row.slug if row is not None else None,
                        hint_id=hint_id,
                        level=hint.get("level") if isinstance(hint, dict) else None,
                        mode=mode, actor=actor)
    db.session.add(unlock)
    audit(ev.id, "member" if mode == UNLOCK_STUDENT else "facilitator", actor,
          "hint_unlock", f"{team.code}/{hint_id}", mode)
    db.session.commit()
    return unlock


def unlock_mode_of(team, hint_id):
    """How this rung came to be open for this team, for the usage record."""
    row = HintUnlock.query.filter_by(team_id=team.id, hint_id=hint_id).one_or_none()
    return row.mode if row is not None else UNLOCK_AUTOMATIC


def next_locked_rung(ev, row, team, ladder):
    """The first rung of one ladder this team cannot open yet, or None."""
    for hint in ladder:
        if not hint_is_open(ev, row, team, hint):
            return hint
    return None


def next_locked_mission_rung(ev, team, content_dir):
    for row in mission_rows(ev):
        if row.state != MISSION_OPEN:
            continue
        definition = content.get_mission(content_dir, row.slug)
        if definition is None:
            continue
        for hint in content.all_hints(definition):
            if not hint_is_open(ev, row, team, hint):
                return {"mission_slug": row.slug, "hint_id": hint["id"],
                        "level": hint["level"],
                        "opens_in": hint_opens_in(ev, row, team, hint)}
    return None


SOURCES_AT_START = 3


def sources_opened(row, team):
    """The ids this team has opened in this mission. Empty before it opens one."""
    if row is None or team is None:
        return set()
    return {t.artifact_id for t in TeamArtifact.query.filter_by(
        team_id=team.id, mission_slug=row.slug).all()}


def visible_source_ids(row, team, definition):
    ids = [a["id"] for a in (definition or {}).get("artifacts") or []
           if a.get("id")]
    if row is None or row.state == MISSION_LOCKED:
        return []
    if row.state == MISSION_CLOSED or (row.opened_at is not None
                                       and row.remaining_seconds() <= 0):
        return ids

    opened = sources_opened(row, team)
    n = min(SOURCES_AT_START, len(ids))
    while n < len(ids) and all(i in opened for i in ids[:n]):
        n += 1
    return ids[:n]


def source_has_arrived(row, team, artifact):
    if row is None:
        return False
    definition = content.get_mission(_content_dir(), row.slug)
    if definition is None:
        return False
    return artifact.get("id") in visible_source_ids(row, team, definition)


def reset_session(ev, actor="facilitator"):
    ev.state = SESSION_CLOSED
    ev.scoreboard_visible = False
    ev.ends_at = utcnow()
    for row in mission_rows(ev):
        if row.state != MISSION_CLOSED:
            row.state = MISSION_CLOSED
            row.closed_at = utcnow()
    audit(ev.id, "facilitator", actor, "session_closed", ev.code)
    db.session.commit()
    return ev
