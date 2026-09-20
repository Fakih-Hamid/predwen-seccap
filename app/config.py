import os

from dotenv import load_dotenv

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(_BASE_DIR, ".env"))

_INSTANCE = os.path.join(_BASE_DIR, "instance")
os.makedirs(_INSTANCE, exist_ok=True)
_DEFAULT_DB = "sqlite:///" + os.path.join(_INSTANCE, "seccap.db").replace("\\", "/")

INSECURE_SECRET = "dev-only-change-me"

BASE_DIR = _BASE_DIR
CONTENT_DIR = os.path.join(_BASE_DIR, "content")
ARTIFACT_DIR = os.path.join(_BASE_DIR, "artifacts")


class Config:
    SECRET_KEY = os.environ.get("SECCAP_SECRET_KEY", INSECURE_SECRET)
    SQLALCHEMY_DATABASE_URI = os.environ.get("SECCAP_DATABASE_URI", _DEFAULT_DB)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SECCAP_COOKIE_SECURE", "").lower() in ("1", "true", "yes")
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12

    TRUSTED_PROXY_COUNT = max(0, int(os.environ.get("SECCAP_TRUSTED_PROXIES") or 0))

    FACILITATOR_PASSWORD_HASH = os.environ.get("SECCAP_FACILITATOR_PASSWORD_HASH", "")

    ASSISTANT_PASSWORD_HASH = os.environ.get("SECCAP_ASSISTANT_PASSWORD_HASH", "")

    DEFAULT_LANG = os.environ.get("SECCAP_DEFAULT_LANG", "ja")

    CONTENT_DIR = CONTENT_DIR
    ARTIFACT_DIR = ARTIFACT_DIR

    ADMIN_BYPASS = os.environ.get("SECCAP_ADMIN_BYPASS", "").lower() in ("1", "true", "yes")

    POLL_SECONDS = int(os.environ.get("SECCAP_POLL_SECONDS", "5"))

    PRESENCE_SECONDS = max(int(os.environ.get("SECCAP_PRESENCE_SECONDS", "15")),
                           2 * POLL_SECONDS)

    ONLINE_WINDOW_SECONDS = PRESENCE_SECONDS + 2 * POLL_SECONDS

    SUBMIT_RPM = int(os.environ.get("SECCAP_SUBMIT_RPM", "60"))

    JOIN_RPM = int(os.environ.get("SECCAP_JOIN_RPM", "60"))

    MAX_TEXT = int(os.environ.get("SECCAP_MAX_TEXT", "2000"))
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024  # no uploads in the MVP; this is the backstop
