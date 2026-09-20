from datetime import datetime, timedelta, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc)


def aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


ROLE_NAVIGATOR = "navigator"
ROLE_OSINT = "osint"
ROLE_INFRA = "infra"
ROLE_CTI = "cti"
ROLE_EVIDENCE = "evidence"
ROLES = (ROLE_NAVIGATOR, ROLE_OSINT, ROLE_INFRA, ROLE_CTI, ROLE_EVIDENCE)

SESSION_SETUP = "setup"
SESSION_BRIEFING = "briefing"
SESSION_RUNNING = "running"
SESSION_FINAL = "final"
SESSION_DEBRIEF = "debrief"
SESSION_CLOSED = "closed"
SESSION_STATES = (SESSION_SETUP, SESSION_BRIEFING, SESSION_RUNNING,
                  SESSION_FINAL, SESSION_DEBRIEF, SESSION_CLOSED)

MISSION_LOCKED = "locked"       # not yet opened by the facilitator
MISSION_OPEN = "open"
MISSION_PAUSED = "paused"
MISSION_CLOSED = "closed"
MISSION_STATES = (MISSION_LOCKED, MISSION_OPEN, MISSION_PAUSED, MISSION_CLOSED)

SRC_AUTO = "auto"
SRC_RUBRIC = "rubric"
SRC_HINT = "hint"
SRC_SPEED = "speed"
SRC_COLLECTIVE = "collective"
SRC_OVERRIDE = "override"

CATEGORIES = ("correctness", "evidence", "reasoning", "completeness",
              "speed", "hint", "collective", "presentation", "response", "other")

AWARDS = ("overall", "evidence", "technical", "response", "teamwork", "presentation")

IOC_TYPES = ("hash", "domain", "ip", "process", "persistence", "file_path")

REQUIRED_IOC_TYPES = ("hash", "domain", "process", "persistence", "file_path")

UNLOCK_AUTOMATIC = "automatic"
UNLOCK_STUDENT = "student_request"
UNLOCK_FACILITATOR = "facilitator"
UNLOCK_MODES = (UNLOCK_AUTOMATIC, UNLOCK_STUDENT, UNLOCK_FACILITATOR)


class EventSession(db.Model):
    __tablename__ = "event_sessions"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(16), unique=True, nullable=False)   # typed by participants
    title = db.Column(db.String(160), nullable=False, default="SECCAP Incident Exercise")
    state = db.Column(db.String(16), nullable=False, default=SESSION_SETUP)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    starts_at = db.Column(db.DateTime)
    ends_at = db.Column(db.DateTime)

    scoreboard_visible = db.Column(db.Boolean, default=False, nullable=False)
    provisional_visible = db.Column(db.Boolean, default=False, nullable=False)
    scores_locked = db.Column(db.Boolean, default=False, nullable=False)
    collective_open = db.Column(db.Boolean, default=False, nullable=False)
    final_open = db.Column(db.Boolean, default=False, nullable=False)

    fallback_snapshots = db.Column(db.Boolean, default=False, nullable=False)

    announcement = db.Column(db.Text)
    announcement_at = db.Column(db.DateTime)

    teams = db.relationship("Team", backref="session", lazy="dynamic",
                            cascade="all, delete-orphan")
    missions = db.relationship("Mission", backref="session", lazy="dynamic",
                               cascade="all, delete-orphan")


class Team(db.Model):
    """An investigation unit. The only thing that is ever scored or ranked."""
    __tablename__ = "teams"
    __table_args__ = (db.UniqueConstraint("session_id", "code", name="uq_team_code"),)

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("event_sessions.id"), nullable=False)
    code = db.Column(db.String(16), nullable=False)
    display_name = db.Column(db.String(80), nullable=False)
    color_key = db.Column(db.String(16), nullable=False, default="teal")
    capacity = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    members = db.relationship("Member", backref="team", lazy="dynamic",
                              cascade="all, delete-orphan")


class Member(db.Model):
    __tablename__ = "members"

    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    nickname = db.Column(db.String(40), nullable=False)
    token = db.Column(db.String(64), unique=True, nullable=False)
    current_role = db.Column(db.String(16), nullable=False, default=ROLE_NAVIGATOR)
    joined_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_seen_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    @property
    def online(self):
        from flask import current_app

        try:
            window = current_app.config["ONLINE_WINDOW_SECONDS"]
        except (RuntimeError, KeyError):     # outside an app context, e.g. a script
            window = 25
        return (utcnow() - aware(self.last_seen_at)) < timedelta(seconds=window)


class Mission(db.Model):
    __tablename__ = "missions"
    __table_args__ = (db.UniqueConstraint("session_id", "slug", name="uq_mission_slug"),)

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("event_sessions.id"), nullable=False)
    slug = db.Column(db.String(40), nullable=False)
    order = db.Column(db.Integer, nullable=False, default=0)
    state = db.Column(db.String(16), nullable=False, default=MISSION_LOCKED)
    duration_seconds = db.Column(db.Integer, nullable=False, default=1800)
    extension_seconds = db.Column(db.Integer, nullable=False, default=0)
    paused_seconds = db.Column(db.Integer, nullable=False, default=0)  # accumulated
    opened_at = db.Column(db.DateTime)
    paused_at = db.Column(db.DateTime)
    closed_at = db.Column(db.DateTime)
    content_version = db.Column(db.String(24))

    @property
    def total_seconds(self):
        return self.duration_seconds + self.extension_seconds

    def elapsed_seconds(self):
        """Wall time inside the mission, pauses removed."""
        if self.opened_at is None:
            return 0
        end = aware(self.closed_at) or utcnow()
        if self.state == MISSION_PAUSED and self.paused_at is not None:
            end = aware(self.paused_at)
        return max(0, int((end - aware(self.opened_at)).total_seconds()) - self.paused_seconds)

    def remaining_seconds(self):
        if self.state == MISSION_LOCKED:
            return self.total_seconds
        return max(0, self.total_seconds - self.elapsed_seconds())

    def past_suggested_time(self):
        return self.state == MISSION_OPEN and self.remaining_seconds() <= 0

    def accepts_writes(self):
        return self.state == MISSION_OPEN


class TeamMission(db.Model):
    __tablename__ = "team_missions"
    __table_args__ = (db.UniqueConstraint("team_id", "mission_slug", name="uq_team_mission"),)

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("event_sessions.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    mission_slug = db.Column(db.String(40), nullable=False)
    first_opened_at = db.Column(db.DateTime)
    submitted_at = db.Column(db.DateTime)
    locked_by = db.Column(db.Integer, db.ForeignKey("members.id"))
    scored_at = db.Column(db.DateTime)     # set once auto-scoring has run; guards double credit


class Observation(db.Model):
    __tablename__ = "observations"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("event_sessions.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    member_id = db.Column(db.Integer, db.ForeignKey("members.id"), nullable=False)
    mission_slug = db.Column(db.String(40), nullable=False)
    artifact_id = db.Column(db.String(80))
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    member = db.relationship("Member")


class Submission(db.Model):
    __tablename__ = "submissions"
    __table_args__ = (db.UniqueConstraint("team_id", "question_key", name="uq_submission"),)

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("event_sessions.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    mission_slug = db.Column(db.String(40), nullable=False)
    question_key = db.Column(db.String(60), nullable=False)
    answer_json = db.Column(db.Text)
    confidence = db.Column(db.String(8))       # low | medium | high
    reasoning = db.Column(db.Text)
    updated_by = db.Column(db.Integer, db.ForeignKey("members.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    evidence = db.relationship("EvidenceLink", backref="submission", lazy="dynamic",
                               cascade="all, delete-orphan")


class EvidenceLink(db.Model):
    __tablename__ = "evidence_links"

    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(db.Integer, db.ForeignKey("submissions.id"), nullable=False)
    artifact_id = db.Column(db.String(80), nullable=False)
    source_url = db.Column(db.String(500))
    excerpt = db.Column(db.Text)
    reasoning = db.Column(db.Text)
    confidence = db.Column(db.String(8))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class HintUsage(db.Model):
    __tablename__ = "hint_usage"
    __table_args__ = (db.UniqueConstraint("team_id", "hint_id", name="uq_hint"),)

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("event_sessions.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    mission_slug = db.Column(db.String(40), nullable=False)
    hint_id = db.Column(db.String(60), nullable=False)
    cost = db.Column(db.Integer, nullable=False, default=0)
    level = db.Column(db.String(16))
    unlock_mode = db.Column(db.String(20), nullable=False, default=UNLOCK_AUTOMATIC)
    requested_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class TeamArtifact(db.Model):
    __tablename__ = "team_artifacts"
    __table_args__ = (db.UniqueConstraint("team_id", "artifact_id", name="uq_team_artifact"),)

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("event_sessions.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    mission_slug = db.Column(db.String(40), nullable=False)
    artifact_id = db.Column(db.String(80), nullable=False)
    opened_at_elapsed = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class HintUnlock(db.Model):
    __tablename__ = "hint_unlocks"
    __table_args__ = (db.UniqueConstraint("team_id", "hint_id", name="uq_hint_unlock"),)

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("event_sessions.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    mission_slug = db.Column(db.String(40))
    hint_id = db.Column(db.String(60), nullable=False)
    level = db.Column(db.String(16))
    mode = db.Column(db.String(20), nullable=False, default=UNLOCK_STUDENT)
    actor = db.Column(db.String(40))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class ScoreEvent(db.Model):
    __tablename__ = "score_events"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("event_sessions.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    mission_slug = db.Column(db.String(40))
    source = db.Column(db.String(16), nullable=False)     # auto | rubric | hint | speed | ...
    category = db.Column(db.String(20), nullable=False)
    points = db.Column(db.Float, nullable=False)          # negative for hints and corrections
    reason = db.Column(db.Text)
    question_key = db.Column(db.String(60))
    actor = db.Column(db.String(40))                      # "server" or the facilitator
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class CollectiveIOC(db.Model):
    __tablename__ = "collective_iocs"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("event_sessions.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    ioc_type = db.Column(db.String(20), nullable=False)   # see IOC_TYPES
    value = db.Column(db.String(200), nullable=False)
    justification = db.Column(db.Text)
    validated = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    team = db.relationship("Team")


class FinalReport(db.Model):
    __tablename__ = "final_reports"
    __table_args__ = (db.UniqueConstraint("team_id", name="uq_final_team"),)

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("event_sessions.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    timeline_json = db.Column(db.Text)        # ordered list of event ids
    verdict = db.Column(db.Text)
    confirmed_facts = db.Column(db.Text)
    inferences = db.Column(db.Text)
    unknowns = db.Column(db.Text)
    scope = db.Column(db.Text)
    response_json = db.Column(db.Text)          # immediate, ordered
    response_future_json = db.Column(db.Text)   # hardening, ordered
    response_other = db.Column(db.Text)        # ordered list of response action ids
    limitations = db.Column(db.Text)
    assembled_at = db.Column(db.DateTime)
    locked_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    team = db.relationship("Team")


class Award(db.Model):
    """A named award the facilitator hands a team at the debrief."""
    __tablename__ = "awards"
    __table_args__ = (db.UniqueConstraint("session_id", "award_key", name="uq_award"),)

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("event_sessions.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    award_key = db.Column(db.String(24), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


TA_SCALE = ("1", "2", "3", "4", "5", "na")

TA_HINT_SCALE = ("never", "rarely", "sometimes", "often", "always",
                 "no_blockage", "na")

TA_FREQUENCY_QUESTIONS = frozenset({"hints"})

TA_QUESTIONS = (
    "participation",     # everyone contributed / one person did all of it
    "discussion",        # they talked before answering
    "split",             # they divided the sources rather than crowding one
    "challenge",         # they questioned each other's conclusions
    "platform",          # they found their way around the interface
    "task",              # they understood what was being asked
    "hints",             # they opened hints when stuck
)


class AssistantReport(db.Model):
    __tablename__ = "assistant_reports"
    __table_args__ = (db.UniqueConstraint("team_id", name="uq_ta_report_team"),)

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("event_sessions.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    answers_json = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    team = db.relationship("Team")


class AuditEvent(db.Model):
    __tablename__ = "audit_events"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("event_sessions.id"))
    actor_type = db.Column(db.String(16), nullable=False)   # facilitator | member | server
    actor_id = db.Column(db.String(60))
    action = db.Column(db.String(60), nullable=False)
    target = db.Column(db.String(120))
    detail = db.Column(db.Text)
    ts = db.Column(db.DateTime, default=utcnow, nullable=False)


def audit(session_id, actor_type, actor_id, action, target=None, detail=None):
    db.session.add(AuditEvent(session_id=session_id, actor_type=actor_type,
                              actor_id=str(actor_id) if actor_id is not None else None,
                              action=action, target=target, detail=detail))
