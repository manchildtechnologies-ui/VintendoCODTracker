"""COD Tweaker: review-first reader/editor for Call of Duty config files.

Read-only by default. Every write path takes a dated safety backup first,
never overwrites a COD config without one, and preserves the read-only lock
state across copies. No memory modification, injection, or executable
patching happens here: this module only parses and rewrites plain-text
config files after the user reviews and confirms each change.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MAX_FILE_BYTES = 4 * 1024 * 1024
BACKUP_KEEP_DEFAULT = 30

SystemRx = re.compile(r"(^|[\\/_.])system([_.].*)?\.(cfg|config|txt|ini|cst)$", re.IGNORECASE)
ProfileRx = re.compile(r"(^|[\\/_.])profile([_.].*)?\.(cfg|config|txt|ini|cst)$", re.IGNORECASE)
OptionsRx = re.compile(r"(^|[\\/_.])options([_.].*)?\.(cfg|config|txt|ini|cst)$", re.IGNORECASE)
GamerProfileRx = re.compile(r"gamerprofile.*\.(cfg|config|txt|ini|cst)$", re.IGNORECASE)
_CONFIG_EXTENSIONS = (".cfg", ".config", ".txt", ".ini", ".cst")
GAME_TOKEN_RX = re.compile(r"cod(\d{2})", re.IGNORECASE)
GAME_TITLES = {"cod26": "MW4"}


def game_token_for(path):
    """Game token like cod26 from a config file name, else empty."""
    match = GAME_TOKEN_RX.search(str(path).lower())
    return ("cod" + match.group(1)) if match else ""


def game_title_for(token):
    """Readable game name for a token; unknown tokens fall back to COD <n>."""
    token = str(token or "").lower()
    if token in GAME_TITLES:
        return GAME_TITLES[token]
    match = re.fullmatch(r"cod(\d+)", token)
    if match:
        return "COD " + str(int(match.group(1)))
    return ""

_SETTING_RX = re.compile(r"^(?P<key>[^=]+?)\s*=\s*(?P<rest>.*)$")
_SECTION_RX = re.compile(r"^\[(?P<name>[^\]]+)\]\s*$")
_COMMENT_SECTION_RX = re.compile(r"^//\s*[=\-~]{2,}\s*(?P<name>.+?)\s*[=\-~]{2,}\s*$")
_PROTECTED_RX = re.compile(r"DO\s*NOT\s*MODIFY", re.IGNORECASE)
_BOOL_TRUE = {"true", "1", "yes", "on"}
_BOOL_FALSE = {"false", "0", "no", "off"}

PERFORMANCE_SECTIONS = {"performance", "cpu", "memory", "network", "streaming", "audio"}


def display_name_for(key_base):
    """Turn ``Renderer.ResolutionScale`` into ``Resolution Scale``; spaced names pass through."""
    text = str(key_base).strip()
    if " " in text:
        return text
    leaf = str(key_base).split(".")[-1]
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", leaf).replace("_", " ").split()
    return " ".join(w[:1].upper() + w[1:] for w in words) or leaf


def category_for(section, hidden):
    """UI tab for a setting: hidden settings get their own tab."""
    if hidden:
        return "Hidden Settings"
    if str(section or "").strip().lower() in PERFORMANCE_SECTIONS:
        return "Performance"
    return "Graphics"


def is_locked(path):
    """A file counts as locked when it is not writable (read-only flag)."""
    try:
        return not bool(os.stat(path).st_mode & stat.S_IWRITE)
    except OSError:
        return False


def set_locked(path, locked):
    """Toggle the read-only flag; in-game menus cannot save while locked."""
    target = Path(path)
    if locked:
        os.chmod(target, stat.S_IREAD)
    else:
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE)


@dataclass
class CODSetting:
    key: str = ""
    name: str = ""
    variant: str = ""
    description: str = ""
    original: str = ""
    pending: object = None
    constraint: str = ""
    quoted: bool = False
    kind: str = "text"
    min: object = None
    max: object = None
    decimals: object = None
    options: object = field(default_factory=list)
    protected: bool = False
    section: str = ""
    file: str = ""
    trailer: str = ""

    @property
    def valueref(self):
        return self.key

    @property
    def effective(self):
        return self.original if self.pending is None else self.pending

    @property
    def modified(self):
        return self.pending is not None and self.pending != self.original

    def range_text(self):
        if self.kind == "choice" and self.options:
            return "one of: " + ", ".join(self.options)
        if self.kind == "number" and (self.min is not None or self.max is not None):
            text = "%s to %s" % (self.min if self.min is not None else "?",
                                 self.max if self.max is not None else "?")
            if self.decimals is not None:
                text += ", decimals %d" % self.decimals
            return text
        if self.kind == "bool":
            return "true / false"
        return ""


def _split_comment(text):
    """Split trailing ``// comment`` from annotations, ignoring ``//`` inside quotes."""
    in_quote = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            in_quote = not in_quote
        elif char == "/" and not in_quote and text[index:index + 2] == "//":
            return text[:index].rstrip(), text[index + 2:].strip()
        index += 1
    return text, ""


def _parse_constraint(body):
    body = (body or "").strip()
    lowered = body.lower()
    min_value = max_value = decimals = None
    options = []
    kind = ""
    if lowered.startswith("one of"):
        kind = "choice"
        options = [part.strip().strip("\"'") for part in body[6:].split(",")]
        options = [part for part in options if part]
    else:
        span = re.search(r"(?P<lo>-?\d+(?:\.\d+)?)\s+to\s+(?P<hi>-?\d+(?:\.\d+)?)", body)
        explicit = re.search(r"min\s*(?P<lo>-?\d+(?:\.\d+)?).{0,8}?max\s*(?P<hi>-?\d+(?:\.\d+)?)", lowered)
        match = span or explicit
        if match:
            kind = "number"
            min_value = float(match.group("lo"))
            max_value = float(match.group("hi"))
        dec = re.search(r"decimals?\s*(?P<d>\d+)", lowered)
        if dec:
            decimals = int(dec.group("d"))
            if not kind:
                kind = "number"
    return kind, min_value, max_value, decimals, options


def _find_closing_quote(text, start):
    """Index of the closing quote, skipping escaped quotes; -1 when unterminated."""
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            return index
        index += 1
    return -1



def parse_text(text, source=""):
    """Parse config text into (settings, section_order). Never raises on bad lines."""
    settings = []
    section = "General"
    pending_comments = []
    lone_slash = False
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            pending_comments = []
            lone_slash = False
            continue
        if stripped.startswith("["):
            header = _SECTION_RX.match(stripped)
            if header:
                section = header.group("name").strip()
                pending_comments = []
            lone_slash = False
            continue
        if stripped.startswith("//"):
            header = _COMMENT_SECTION_RX.match(stripped)
            if header:
                section = header.group("name").strip()
                pending_comments = []
                lone_slash = False
            elif stripped == "//":
                lone_slash = True
            elif lone_slash:
                section = stripped[2:].strip()
                pending_comments = []
                lone_slash = False
            else:
                pending_comments.append(stripped[2:].strip())
                lone_slash = False
            continue
        lone_slash = False
        if "=" not in stripped:
            pending_comments = []
            continue
        key, _, rest = stripped.partition("=")
        key = key.strip()
        if not key:
            pending_comments = []
            continue
        description = " ".join(p for p in pending_comments if p)
        pending_comments = []
        base, _, variant = key.partition("@")
        base = base.strip()
        variant = variant.split(";", 1)[0].strip()
        value_part, trailing = _split_comment(rest)
        value_part = value_part.strip()
        quoted = False
        if value_part.startswith('"'):
            end = _find_closing_quote(value_part, 0)
            if end == -1:
                continue
            value = value_part[1:end]
            quoted = True
        elif value_part:
            value = value_part.split(None, 1)[0]
        else:
            value = ""
        constraint = trailing.strip()
        kind, minimum, maximum, decimals, options = _parse_constraint(constraint)
        if quoted:
            kind = "quoted"
        elif kind == "":
            lowered = value.strip().lower()
            if lowered in _BOOL_TRUE or lowered in _BOOL_FALSE:
                kind = "bool"
            else:
                try:
                    float(value)
                except (TypeError, ValueError):
                    pass
                else:
                    kind = "number"
        if kind == "number" and decimals is None:
            decimals = len(value.split(".", 1)[1]) if "." in value else 0
        if kind == "bool":
            canonical = value.strip().lower()
            if canonical in _BOOL_TRUE:
                value = "true"
            elif canonical in _BOOL_FALSE:
                value = "false"
        settings.append(CODSetting(
            key=key, name=display_name_for(base), variant=variant,
            description=description, original=value, constraint=constraint,
            quoted=quoted, kind=kind, min=minimum, max=maximum,
            decimals=decimals, options=options,
            protected=bool(_PROTECTED_RX.search(trailing)),
            section=section, trailer=trailing,
        ))
    return settings


class CODDocument:
    """One parsed config file with staged (pending) edits kept off disk."""

    def __init__(self, path, raw, settings):
        self.path = Path(path)
        self.loaded_bytes = bytes(raw)
        self.settings = list(settings)
        self.newline = "\r\n" if b"\r\n" in bytes(raw) else "\n"

    @classmethod
    def load(cls, path):
        target = Path(path)
        raw = target.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("Config file is larger than 4MB; refusing to parse: %s" % target)
        text = raw.decode("utf-8-sig", errors="replace")
        return cls(target, raw, parse_text(text, source=str(target)))

    def by_ref(self):
        return {item.key: item for item in self.settings}

    def match(self, key=None, name=None, variant=""):
        """Exact-key first, then name+variant fallback (case-insensitive)."""
        if key:
            for item in self.settings:
                if item.key == key:
                    return item
            lowered = str(key).lower()
            for item in self.settings:
                if item.key.lower() == lowered:
                    return item
        if name:
            want_variant = str(variant or "").lower()
            for item in self.settings:
                base = item.key.partition("@")[0]
                if item.name.lower() == str(name).lower() and item.variant.lower() == want_variant:
                    return item
                if base.lower() == str(name).lower() and item.variant.lower() == want_variant:
                    return item
        return None

    def validate(self, setting, raw_value):
        """Return (ok, normalized, error); protected settings always reject."""
        text = str(raw_value).strip()
        if setting.protected:
            return False, setting.original, "DO NOT MODIFY: this setting is protected"
        if setting.kind == "bool":
            lowered = text.lower().strip("\"'")
            if lowered in _BOOL_TRUE:
                return True, "true", ""
            if lowered in _BOOL_FALSE:
                return True, "false", ""
            return False, setting.original, "Expected a boolean (true/false)"
        if setting.kind == "number":
            try:
                number = float(text)
            except (TypeError, ValueError):
                return False, setting.original, "Expected a number, got %r" % raw_value
            if setting.min is not None and number < setting.min - 1e-9:
                return False, setting.original, "Below minimum %s" % setting.min
            if setting.max is not None and number > setting.max + 1e-9:
                return False, setting.original, "Above maximum %s" % setting.max
            if setting.decimals is not None:
                number = round(number, setting.decimals)
                normalized = ("%.*f" % (setting.decimals, number))
            elif number.is_integer():
                normalized = str(int(number))
            else:
                normalized = str(number)
            return True, normalized, ""
        if setting.kind == "choice":
            for option in setting.options:
                if text.lower() == option.lower():
                    return True, option, ""
            return False, setting.original, "Expected one of: %s" % ", ".join(setting.options)
        inner = text
        if setting.quoted and len(inner) >= 2 and inner.startswith('"') and inner.endswith('"'):
            inner = inner[1:-1]
        return True, inner, ""

    def stage(self, key, raw_value):
        setting = self.match(key=key)
        if setting is None:
            return False, "", "Unknown setting: %s" % key
        ok, normalized, error = self.validate(setting, raw_value)
        if not ok:
            return False, setting.original, error
        setting.pending = normalized
        return True, normalized, ""

    def revert_to_original(self, key):
        setting = self.match(key=key)
        if setting is None:
            return False
        setting.pending = None
        return True

    def revert_to_disk(self, key):
        """Stage whatever value is currently on disk (reloads bytes first)."""
        setting = self.match(key=key)
        if setting is None:
            return False, "Unknown setting: %s" % key
        try:
            disk = CODDocument.load(self.path)
        except OSError as exc:
            return False, str(exc)
        other = disk.match(key=setting.key, name=setting.name, variant=setting.variant)
        if other is None:
            return False, "Setting is no longer present on disk"
        ok, normalized, error = self.validate(setting, other.original)
        if not ok:
            return False, error
        setting.pending = normalized
        return True, ""

    def refresh_keep_pending(self):
        """Reload from disk after an external change, keeping staged edits."""
        staged = {item.key: item.pending for item in self.settings if item.pending is not None}
        fresh = CODDocument.load(self.path)
        for item in fresh.settings:
            if item.key in staged:
                ok, normalized, _ = fresh.validate(item, staged[item.key])
                item.pending = normalized if ok else staged[item.key]
        self.loaded_bytes = fresh.loaded_bytes
        self.settings = fresh.settings
        return self

    def is_stale_on_disk(self):
        try:
            return self.path.read_bytes() != self.loaded_bytes
        except OSError:
            return True

    def staged_changes(self):
        return [item for item in self.settings if item.modified]

    def _format_value(self, setting, value):
        if setting.quoted or setting.kind == "quoted":
            return '"%s"' % value
        return value

    def render_bytes(self):
        """Rebuild file text; only lines with staged edits change."""
        try:
            text = self.loaded_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = self.loaded_bytes.decode("utf-8-sig", errors="replace")
        lines = text.splitlines()
        changed = {item.key: item for item in self.settings if item.modified}
        if not changed:
            return self.loaded_bytes
        ordered = sorted(changed.values(), key=lambda item: self.settings.index(item))
        seen_counts = {}
        for setting in ordered:
            occurrence = 0
            for index, line in enumerate(lines):
                match = _SETTING_RX.match(line.strip())
                if match and match.group("key") == setting.key:
                    if occurrence == seen_counts.get(setting.key, 0):
                        rebuilt = "%s = %s" % (setting.key, self._format_value(setting, setting.pending))
                        trailer = getattr(setting, "trailer", "") or setting.constraint
                        if trailer:
                            rebuilt += " // " + trailer
                        lines[index] = rebuilt
                        seen_counts[setting.key] = seen_counts.get(setting.key, 0) + 1
                        break
                    occurrence += 1
        return (self.newline.join(lines) + self.newline).encode("utf-8")

    def save(self):
        """Write staged edits; callers must back up first via BackupStore."""
        if not self.staged_changes():
            return False
        locked = is_locked(self.path)
        if locked:
            set_locked(self.path, False)
        try:
            self.path.write_bytes(self.render_bytes())
        finally:
            if locked:
                set_locked(self.path, True)
        self.loaded_bytes = self.path.read_bytes()
        for item in self.settings:
            if item.modified:
                item.original = item.pending
                item.pending = None
        return True


class NotesRegistry:
    """Shipped notes plus a user override file merged on top (override wins)."""

    def __init__(self, shipped_path=None, override_path=None):
        self.entries = {}
        for source in (shipped_path, override_path):
            if not source:
                continue
            try:
                data = json.loads(Path(source).read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                self.entries.update(data)

    def get(self, key="", name=""):
        if key and key in self.entries:
            return self.entries[key]
        for probe in (str(key or "").lower(), str(name or "").lower()):
            if probe and probe in self.entries:
                return self.entries[probe]
        if name:
            lowered = str(name).lower()
            for stored_key, entry in self.entries.items():
                if str(stored_key).lower() == lowered:
                    return entry
        return {"note": "", "hidden": False, "suggested": ""}

    def note_for(self, setting):
        entry = self.get(setting.key, setting.name)
        text = str(entry.get("note", "") or "")
        if setting.description:
            text = (setting.description + " " + text).strip() if text else setting.description
        if not text:
            text = self._fallback_description(setting)
        return {"note": text, "hidden": bool(entry.get("hidden", False)),
                "suggested": str(entry.get("suggested", "") or "")}

    @staticmethod
    def _fallback_description(setting):
        label = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(setting.name or setting.key or "setting")).strip()
        kind = {"bool": "True/false toggle", "number": "Numeric value",
                "choice": "Choice", "text": "Text value"}.get(setting.kind, "Setting")
        text = "%s '%s'" % (kind, label if label else "setting")
        span = setting.range_text()
        if span:
            text += " (%s)" % span
        return text + ". No shipped description; change carefully."


class Preset:
    """Review-first preset: load stages nothing until the user confirms."""

    def __init__(self, payload):
        self.payload = dict(payload or {})
        values = self.payload.get("Values", [])
        self.values = list(values) if isinstance(values, list) else []

    @classmethod
    def load(cls, path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ValueError("Preset could not be read: %s" % exc)
        if not isinstance(data, dict) or not isinstance(data.get("Values"), list):
            raise ValueError("Preset must be an object with a 'Values' list")
        return cls(data)

    @property
    def title(self):
        return str(self.payload.get("Title") or self.payload.get("Kind") or "Preset")

    def match_against(self, document):
        """Pair each preset value with a document setting; protected are skipped."""
        pairs = []
        for entry in self.values:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("Key") or "").strip()
            name = str(entry.get("Name") or "").strip()
            variant = str(entry.get("Variant") or "").strip()
            setting = document.match(key=key or None, name=name or None, variant=variant)
            if setting is not None and not key and not name:
                setting = None
            if setting is None and (key or name):
                pairs.append({"entry": entry, "setting": None,
                              "skipped_protected": False, "reason": "not present in this file"})
                continue
            if setting is not None and setting.protected:
                pairs.append({"entry": entry, "setting": setting,
                              "skipped_protected": True, "reason": "protected (DO NOT MODIFY)"})
                continue
            pairs.append({"entry": entry, "setting": setting,
                          "skipped_protected": False, "reason": ""})
        return pairs

    def dry_run(self, document):
        """Describe what confirm would stage, without staging anything."""
        plan = []
        for pair in self.match_against(document):
            setting, entry = pair["setting"], pair["entry"]
            if setting is None or pair["skipped_protected"]:
                plan.append({"key": str(entry.get("Key") or entry.get("Name")),
                             "action": "skip", "reason": pair["reason"]})
                continue
            ok, normalized, error = document.validate(setting, entry.get("Value", ""))
            if ok and normalized != setting.effective:
                plan.append({"key": setting.key, "action": "stage",
                             "from": setting.effective, "to": normalized, "reason": ""})
            elif ok:
                plan.append({"key": setting.key, "action": "noop",
                             "reason": "already set"})
            else:
                plan.append({"key": setting.key, "action": "skip", "reason": error})
        return plan

    def confirm(self, document, pairs=None):
        """Stage matched values after user review; returns (staged, skipped)."""
        staged, skipped = [], []
        for pair in pairs if pairs is not None else self.match_against(document):
            setting, entry = pair["setting"], pair["entry"]
            if setting is None or pair["skipped_protected"]:
                skipped.append({"key": str(entry.get("Key") or entry.get("Name")),
                                "reason": pair["reason"]})
                continue
            ok, normalized, error = document.validate(setting, entry.get("Value", ""))
            if not ok:
                skipped.append({"key": setting.key, "reason": error})
                continue
            if normalized != setting.effective:
                setting.pending = normalized
                staged.append({"key": setting.key, "to": normalized})
        return staged, skipped


class Diff:
    """ValueRef-keyed comparison between mine and theirs."""

    def __init__(self, rows):
        self.rows = list(rows)

    @classmethod
    def build(cls, mine, theirs):
        rows = []
        for ref in sorted(set(mine) | set(theirs)):
            in_mine, in_theirs = ref in mine, ref in theirs
            if in_mine and in_theirs:
                status = "matching" if mine[ref] == theirs[ref] else "different"
            elif in_theirs:
                status = "only-in-other"
            else:
                status = "only-in-mine"
            rows.append({"ref": ref, "mine": mine.get(ref, ""),
                         "theirs": theirs.get(ref, ""), "status": status})
        return cls(rows)

    def summary(self):
        counts = {"matching": 0, "different": 0, "only-in-other": 0, "only-in-mine": 0}
        for row in self.rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return counts

    def take(self, document, ref):
        """Adopt their value as a staged pending edit."""
        row = next((item for item in self.rows if item["ref"] == ref), None)
        if row is None or row["theirs"] == "":
            return False, "No incoming value for %s" % ref
        ok, normalized, error = document.stage(ref, row["theirs"])
        return ok, normalized if ok else error


class BackupStore:
    """Timestamped safety copies; keeps the newest 30 per file, oldest pruned."""

    def __init__(self, root, keep=BACKUP_KEEP_DEFAULT):
        self.root = Path(root)
        self.keep = int(keep)

    def _profile_dir(self, profile):
        folder = self.root / str(profile or "default")
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def backup_file(self, path, profile="default"):
        source = Path(path)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
        target = self._profile_dir(profile) / ("%s_%s" % (stamp, source.name))
        locked = is_locked(source)
        shutil.copy2(source, target)
        if locked:
            set_locked(source, True)
        self.prune(profile, source.name)
        return target

    def list_backups(self, profile=None, basename=None):
        folders = [self.root / str(profile)] if profile else (
            sorted(p for p in self.root.iterdir() if p.is_dir()) if self.root.is_dir() else [])
        found = []
        for folder in folders:
            if not folder.is_dir():
                continue
            for candidate in folder.iterdir():
                if candidate.is_file() and (basename is None or candidate.name.endswith("_" + basename)
                                            or candidate.name == basename):
                    found.append(candidate)
        return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)

    def prune(self, profile, basename):
        matches = [p for p in self.list_backups(profile=profile)
                   if p.name.endswith("_" + basename) or p.name == basename]
        doomed = sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True)[self.keep:]
        for victim in doomed:
            try:
                victim.unlink()
            except OSError:
                pass
        return len(doomed)

    def restore(self, backup, target):
        """Restore always takes a fresh safety backup first and keeps the lock."""
        source, destination = Path(backup), Path(target)
        if not source.is_file():
            raise FileNotFoundError("Backup was not found: %s" % source)
        safety = self.backup_file(destination)
        locked = is_locked(destination)
        if locked:
            set_locked(destination, False)
        try:
            shutil.copy2(source, destination)
        finally:
            if locked:
                set_locked(destination, True)
        return safety


class PinStore:
    """Pinned settings keyed by lowercased ValueRef, persisted as JSON."""

    def __init__(self, path):
        self.path = Path(path)
        self.pins = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                self.pins = {str(k).lower(): v for k, v in data.items()}
        except (OSError, ValueError):
            self.pins = {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.pins, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def is_pinned(self, ref):
        return str(ref).lower() in self.pins

    def toggle(self, ref):
        lowered = str(ref).lower()
        if lowered in self.pins:
            del self.pins[lowered]
            pinned = False
        else:
            self.pins[lowered] = str(ref)
            pinned = True
        self.save()
        return pinned

    def pinned_refs(self):
        return sorted(self.pins.values())


class CODTweakerLocator:
    """Find local COD config files: LocalAppData players first, Documents fallback."""

    def __init__(self, roots=None):
        self.roots = list(roots) if roots is not None else default_roots()

    @staticmethod
    def classify(path):
        name = str(path)
        if GamerProfileRx.search(name):
            return "gamerprofile"
        if SystemRx.search(name):
            return "system"
        if ProfileRx.search(name):
            return "profile"
        if OptionsRx.search(name):
            return "options"
        return "other"

    @staticmethod
    def sniff(path):
        try:
            if Path(path).stat().st_size > MAX_FILE_BYTES:
                return False
            text = Path(path).read_bytes()[:8192].decode("utf-8-sig", errors="replace")
        except OSError:
            return False
        probed = 0
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("["):
                continue
            if _SETTING_RX.match(stripped):
                return True
            probed += 1
            if probed > 40:
                return False
        return False

    def find(self):
        found = []
        for root in self.roots:
            try:
                candidates = sorted(Path(root).rglob("*"))
            except OSError:
                continue
            for candidate in candidates:
                try:
                    low_suffix = candidate.suffix.lower()
                    if not candidate.is_file() or (low_suffix not in _CONFIG_EXTENSIONS and not re.fullmatch(r"\.txt\d+", low_suffix)):
                        continue
                    if candidate.stat().st_size > MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                kind = self.classify(candidate)
                if not self.sniff(candidate):
                    continue
                parent = candidate.parent.name
                group = "Game settings" if parent.lower() == "players" or candidate.parent == root else "Player " + parent
                token = game_token_for(candidate.name)
                found.append({"kind": kind, "path": candidate, "group": group, "game": game_title_for(token), "token": token})
        order = {"system": 0, "gamerprofile": 1, "profile": 2, "options": 3, "other": 4}
        return sorted(found, key=lambda item: (order.get(item["kind"], 9), str(item["path"])))


def default_roots():
    roots = []
    local_app = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app and (Path(local_app) / "Activision").is_dir():
        roots.append(Path(local_app) / "Activision")
    documents = Path.home() / "Documents"
    for leaf in ("Call of Duty", "CallOfDuty"):
        if (documents / leaf).is_dir():
            roots.append(documents / leaf)
    return roots


_GAME_PREFIXES = ("cod", "callofduty", "modernwarfare", "blackops", "vanguard", "warzone", "iw")
_NON_GAME_NAMES = ("code", "codium", "coder", "cody", "codon", "codtweaker")
_NON_GAME_PREFIXES = _NON_GAME_NAMES


def matching_game_process(prefixes=_GAME_PREFIXES):
    """Name of the running process that looks like the COD game, else ""."""
    try:
        import psutil
    except ImportError:
        return ""
    try:
        processes = list(psutil.process_iter(["name"]))
    except Exception:
        return ""
    for proc in processes:
        try:
            name = str((proc.info or {}).get("name") or "").lower()
        except Exception:
            continue
        base = name[:-4] if name.endswith(".exe") else name
        if "munity" in base:
            continue
        if any(base == name or base.startswith(name) for name in _NON_GAME_PREFIXES):
            continue
        if any(base == prefix or base.startswith(prefix) for prefix in prefixes):
            return name
    return ""


def is_game_running(prefixes=_GAME_PREFIXES):
    """True when a process looks like the COD game (excludes editors/helpers)."""
    return bool(matching_game_process(prefixes))


def files_differ(first_bytes, second_bytes):
    return bytes(first_bytes or b"") != bytes(second_bytes or b"")


def unique_backup_name(original_name, when=None):
    moment = when or datetime.now()
    return "%s_%s" % (moment.strftime("%Y-%m-%d_%H-%M-%S-%f"), original_name)


def preset_template_path():
    return "cod_tweaker_presets.json"


def _self_test_sample():
    return """// == Graphics ==
// Overall graphics preset.
Renderer.Quality@Variant0 "High" (one of Low, Medium, High) // Overall graphics preset
// Render resolution scale.
Renderer.ResolutionScale 1.000 (0.500 to 2.000, decimals 3)
Renderer.VSync true
[Performance]
// Worker thread count.
System.ThreadCount 8 (1 to 64)
// == Hidden ==
Hidden.DebugOverlay 0 DO NOT MODIFY
"""


if __name__ == "__main__":
    document_settings = parse_text(_self_test_sample(), source="<sample>")
    assert [item.key for item in document_settings] == [
        "Renderer.Quality@Variant0", "Renderer.ResolutionScale", "Renderer.VSync",
        "System.ThreadCount", "Hidden.DebugOverlay"]
    assert document_settings[0].kind == "choice"
    assert document_settings[3].min == 1 and document_settings[3].max == 64
    assert document_settings[4].protected
    print("cod_tweaker self-test ok (%d settings)" % len(document_settings))
