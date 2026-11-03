"""Contest settings that admins can change from the panel (platform/API.md,
"Admin API"): stored as JSON in the `settings` table, overriding the values
from the environment. A missing row means "use the environment value".

Hidden/public seeds, the time limit, memory and source size are deliberately
not editable: changing them would invalidate existing scores.
"""

import dataclasses
import json
import re
import secrets
import string

from . import config, db

# panel key -> Settings field
FIELDS = {
    "name": "contest_name",
    "start": "start",
    "end": "end",
    "freeze": "freeze",
    "invite": "invite",
    "full_per_hour": "full_per_hour",
    "announcement": "announcement",
    "announcement_on": "announcement_on",
}
TIME_KEYS = ("start", "end", "freeze")
INVITE_RE = re.compile(r"^[\x21-\x7e]{4,64}$")


class SettingsError(ValueError):
    pass


def stored(conn):
    out = {}
    for r in conn.execute("SELECT key, value FROM settings"):
        if r["key"] in FIELDS:
            try:
                out[r["key"]] = json.loads(r["value"])
            except ValueError:
                continue
    return out


def _to_field(key, value):
    """JSON value (as stored) -> Settings field value."""
    if key in TIME_KEYS:
        return config.parse_time(value, key) if value else None
    return value


def _to_json(key, value):
    """Settings field value -> JSON value (as returned by the API)."""
    if key in TIME_KEYS:
        return db.iso(value) if value else None
    return value


def apply(settings, values):
    """Settings with the given {key: stored json value} applied."""
    changes = {}
    for key, value in values.items():
        try:
            changes[FIELDS[key]] = _to_field(key, value)
        except (config.ConfigError, KeyError):
            continue
    return dataclasses.replace(settings, **changes) if changes else settings


def effective(conn, settings):
    """The env Settings with the admin overrides from the DB applied."""
    return apply(settings, stored(conn))


def mask_invite(code):
    if not code:
        return "(open)"
    if len(code) <= 6:
        return "…" + code[-1:]
    return code[:2] + "…" + code[-2:]


def validate(key, value):
    """API value -> normalised JSON value to store; raises SettingsError."""
    if key not in FIELDS:
        raise SettingsError(f"unknown setting {key!r}")
    if key == "name":
        if not isinstance(value, str) or not (1 <= len(value.strip()) <= 80):
            raise SettingsError("name must be 1-80 characters")
        return value.strip()
    if key in TIME_KEYS:
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise SettingsError(f"{key} must be an ISO 8601 time or null")
        try:
            return db.iso(config.parse_time(value, key))
        except config.ConfigError:
            raise SettingsError(f"{key}: not an ISO 8601 time: {value!r}") from None
    if key == "invite":
        if not isinstance(value, str):
            raise SettingsError("invite must be a string")
        value = value.strip()
        if value and not INVITE_RE.match(value):
            raise SettingsError("invite code must be 4-64 printable characters without spaces (or empty for open registration)")
        return value
    if key == "full_per_hour":
        if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= 1000):
            raise SettingsError("full_per_hour must be an integer from 0 to 1000")
        return value
    if key == "announcement":
        if not isinstance(value, str) or len(value) > 500:
            raise SettingsError("announcement must be at most 500 characters")
        return value.strip()
    if key == "announcement_on":
        if not isinstance(value, bool):
            raise SettingsError("announcement_on must be true or false")
        return value
    raise SettingsError(f"unknown setting {key!r}")  # pragma: no cover


def check_times(s):
    if s.start and s.end and not s.start < s.end:
        raise SettingsError("the end must be after the start")
    if s.freeze:
        if s.start and s.freeze < s.start:
            raise SettingsError("the freeze must not be before the start")
        if s.end and s.freeze >= s.end:
            raise SettingsError("the freeze must be before the end")


def describe(conn, settings):
    """GET /api/admin/settings."""
    values = stored(conn)
    eff = apply(settings, values)
    editable = {}
    for key, field in FIELDS.items():
        editable[key] = {"value": _to_json(key, getattr(eff, field)),
                         "env": _to_json(key, getattr(settings, field)),
                         "overridden": key in values}
    return {
        "editable": editable,
        "readonly": {
            "hidden_count": len(settings.hidden_seeds),
            "public_seeds": list(settings.public_seeds),
            "time_limit": settings.time_limit,
            "memory_mb": settings.memory_mb,
            "max_source_kb": settings.max_source_kb,
            "sandbox": settings.sandbox,
            "workers": settings.workers,
        },
    }


def _audit_value(key, value):
    return mask_invite(value) if key == "invite" else value


def update(conn, settings, actor, set_values=None, clear=()):
    """Validate and store changes atomically; returns the audit details {key: [old, new]}
    (empty when nothing changed). Must be called inside a transaction."""
    set_values = dict(set_values or {})
    clear = list(clear or ())
    for key in clear:
        if key not in FIELDS:
            raise SettingsError(f"unknown setting {key!r}")
    both = set(set_values) & set(clear)
    if both:
        raise SettingsError(f"can't both set and clear {sorted(both)[0]!r}")
    normalised = {k: validate(k, v) for k, v in set_values.items()}
    before = stored(conn)
    after = {k: v for k, v in before.items() if k not in clear}
    after.update(normalised)
    old_eff, new_eff = apply(settings, before), apply(settings, after)
    check_times(new_eff)
    t = db.now_iso()
    for key in clear:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    for key, value in normalised.items():
        conn.execute("INSERT INTO settings(key, value, updated, updated_by) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated = excluded.updated, "
                     "updated_by = excluded.updated_by", (key, json.dumps(value), t, actor))
    details = {}
    for key, field in FIELDS.items():
        old, new = _to_json(key, getattr(old_eff, field)), _to_json(key, getattr(new_eff, field))
        if old != new or (key in before) != (key in after):
            entry = [_audit_value(key, old), _audit_value(key, new)]
            if key in clear:
                entry.append("reset to env")
            details[key] = entry
    return details


def new_invite():
    alphabet = string.ascii_lowercase + string.digits
    alphabet = "".join(c for c in alphabet if c not in "0o1l")
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))
