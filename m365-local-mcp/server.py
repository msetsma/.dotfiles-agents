"""Read-only MCP server for local M365 data on macOS.

Three data sources, none of which need Microsoft Graph consent or Full Disk Access:

  * meetings -> Calendar.app via AppleScript
  * mail     -> Mail.app via AppleScript
  * files    -> OneDrive-synced SharePoint libraries on disk

Everything here is read-only by design. There are no tools that send mail,
create events, or write files.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# mcp SDK 2.0 renamed FastMCP to MCPServer; the decorator API is unchanged.
# Import path matches the existing house pattern in dev/kan-setup/bin/kan-mcp.py.
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

mcp = MCPServer(
    name="m365-local",
    version="0.1.0",
    instructions=(
        "Read-only access to local M365 data: Outlook meetings (Calendar.app), "
        "mail search (Mail.app), and OneDrive-synced SharePoint files. "
        "Prefer mail_search(field='subject') -- it is sub-second. field='body' "
        "fetches message bodies one at a time and is far slower, so always pass "
        "`days`. sp_find never downloads anything; sp_read downloads on demand."
    ),
)

# Every tool here is a read. Advertise that to clients so they can skip
# confirmation prompts and never treat a call as mutating.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)

# Field / record separators. Subjects routinely contain "|", ",", tabs and
# newlines, so any printable delimiter eventually corrupts parsing. ASCII
# unit/record separators never appear in real mail text.
FS = "\x1f"
RS = "\x1e"

MAIL_ACCOUNT = os.environ.get("M365_MAIL_ACCOUNT", "Exchange")

# AppleScript error codes worth translating into human instructions.
ERR_NOT_AUTHORIZED = "-1743"  # user denied the Automation prompt
ERR_TIMED_OUT = "-1712"  # AppleEvent timed out


class OsaError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# AppleScript plumbing
# --------------------------------------------------------------------------

# Shared handlers, prepended to every script. `my pad`/`my iso` are callable
# from inside `tell` blocks. AppleScript's default date coercion produces
# locale strings ("Thursday, August 20, 2026 at 8:30:00 AM"); ISO is parseable.
OSA_PRELUDE = """
on pad(n)
    set s to n as string
    if length of s < 2 then return "0" & s
    return s
end pad

on iso(d)
    if d is missing value then return ""
    return (year of d as string) & "-" & my pad(month of d as integer) & "-" & ¬
        my pad(day of d) & "T" & my pad(hours of d) & ":" & ¬
        my pad(minutes of d) & ":" & my pad(seconds of d)
end iso

on clean(v)
    if v is missing value then return ""
    return v as string
end clean
"""


def osa(script: str, timeout: int = 60) -> str:
    """Run an AppleScript and return stdout.

    The script is fed over stdin rather than -e so that multi-line scripts,
    quotes and the ¬ continuation character survive untouched.
    """
    try:
        proc = subprocess.run(
            ["osascript"],
            input=OSA_PRELUDE + script,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise OsaError(
            f"AppleScript exceeded {timeout}s. For body searches, narrow `days` "
            "or lower `limit`; for meetings, narrow the date window."
        ) from None

    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if ERR_NOT_AUTHORIZED in err:
            raise OsaError(
                "macOS denied Automation access. Grant it in System Settings > "
                "Privacy & Security > Automation, then retry."
            )
        if ERR_TIMED_OUT in err:
            raise OsaError(
                "Mail/Calendar stopped responding (AppleEvent timeout). The app "
                "may be mid-sync; retry with a narrower query."
            )
        raise OsaError(err or f"osascript exited {proc.returncode}")
    return proc.stdout


def parse_records(raw: str, n_fields: int) -> list[list[str]]:
    """Split RS/FS-delimited osascript output into rows.

    Rows with the wrong arity are dropped rather than silently misaligned --
    a shifted column is worse than a missing row.
    """
    rows = []
    for chunk in raw.split(RS):
        if not chunk.strip():
            continue
        fields = chunk.split(FS)
        if len(fields) == n_fields:
            rows.append(fields)
    return rows


def esc(s: str) -> str:
    """Quote a Python string for embedding in an AppleScript literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def mailbox_ref(mailbox: str) -> str:
    """AppleScript reference for a mailbox name.

    "Inbox" maps to Mail's special unified `inbox`; everything else is
    addressed through the account.
    """
    if mailbox.strip().lower() == "inbox":
        return "inbox"
    return f'mailbox "{esc(mailbox)}" of account "{esc(MAIL_ACCOUNT)}"'


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
def meetings_list(
    days_back: int = 1,
    days_ahead: int = 7,
    calendar: str | None = None,
    include_attendees: bool = False,
) -> dict[str, Any]:
    """List calendar events in a date window.

    Work meetings sync from Exchange into Calendar.app. Pass `calendar` to
    restrict to one calendar name; omit it to search all of them.

    Args:
        days_back: How many days before now to include.
        days_ahead: How many days after now to include.
        calendar: Optional calendar name filter (exact match).
        include_attendees: Fetch attendee names/emails. Slower -- one extra
            round-trip per event.
    """
    cal_filter = ""
    if calendar:
        # `considering case` because AppleScript string comparison is
        # case- and diacritic-insensitive by default, and calendar names are
        # not unique -- two can differ by case alone.
        cal_filter = (
            f'considering case\n'
            f'                    if cn is not "{esc(calendar)}" then exit repeat\n'
            f'                end considering'
        )

    att_block = 'set att to ""'
    if include_attendees:
        # Nested loop over attendees; names and emails joined with ";".
        att_block = """
                set att to ""
                try
                    repeat with k from 1 to count of attendees of e
                        set a to attendee k of e
                        set att to att & my clean(display name of a) & "<" & ¬
                            my clean(email address of a) & ">;"
                    end repeat
                end try"""

    script = f"""
set out to ""
tell application "Calendar"
    with timeout of 180 seconds
        set d1 to (current date) - ({int(days_back)} * days)
        set d2 to (current date) + ({int(days_ahead)} * days)
        repeat with i from 1 to count of calendars
            set c to calendar i
            set cn to name of c
            repeat 1 times
                {cal_filter}
                try
                    set evs to (every event of c whose start date > d1 and start date < d2)
                    repeat with j from 1 to count of evs
                        set e to item j of evs
                        {att_block}
                        set out to out & cn & "{FS}" & my iso(start date of e) & "{FS}" & ¬
                            my iso(end date of e) & "{FS}" & my clean(summary of e) & "{FS}" & ¬
                            my clean(location of e) & "{FS}" & my clean(uid of e) & "{FS}" & ¬
                            att & "{RS}"
                    end repeat
                end try
            end repeat
        end repeat
    end timeout
end tell
return out
"""
    # Capture the window BEFORE running the script. AppleScript takes its own
    # `current date` at script start and this call can run for minutes, so
    # computing the bounds afterwards would shift them and mislabel events that
    # sit near a boundary.
    now = datetime.now()
    lo = now - timedelta(days=days_back)
    hi = now + timedelta(days=days_ahead)

    rows = parse_records(osa(script, timeout=200), 7)

    # Calendar.app matches recurring events by their occurrences but reports the
    # SERIES start date, so a weekly meeting that began in January shows a
    # January date inside an August window. Fetching the `recurrence` rule to
    # resolve the real occurrence is not viable -- it timed out past 5 minutes
    # on a single calendar. Flag these instead of returning a misleading date.
    # A small grace margin absorbs the sub-second skew between AppleScript's
    # clock read and ours, so a boundary event is not falsely called recurring.
    lo_check = lo - timedelta(minutes=1)
    hi_check = hi + timedelta(minutes=1)

    events = []
    recurring = 0
    for cal, start, end, summary, location, uid, att in rows:
        ev: dict[str, Any] = {
            "calendar": cal,
            "start": start,
            "end": end,
            "summary": summary,
            "location": location,
            "uid": uid,
        }
        try:
            in_window = lo_check <= datetime.fromisoformat(start) <= hi_check
        except ValueError:
            in_window = True  # unparseable date: do not claim anything about it
        if not in_window:
            ev["recurring_series"] = True
            ev["start_note"] = (
                "This is a recurring event; `start` is the series' first "
                "occurrence, not the one falling in the requested window."
            )
            recurring += 1
        events.append(ev)
        if include_attendees:
            ev["attendees"] = [a for a in att.split(";") if a]

    events.sort(key=lambda e: e["start"])
    out: dict[str, Any] = {
        "window": {
            "days_back": days_back,
            "days_ahead": days_ahead,
            "from": lo.isoformat(timespec="seconds"),
            "to": hi.isoformat(timespec="seconds"),
        },
        "count": len(events),
        "events": events,
    }
    if recurring:
        out["recurring_series_count"] = recurring
        out["note"] = (
            f"{recurring} event(s) are recurring and show their series start date. "
            "They do occur in the requested window; the exact occurrence time is "
            "not available through Calendar.app's AppleScript interface."
        )
    return out


@mcp.tool(annotations=READ_ONLY)
def calendars_list() -> dict[str, Any]:
    """List calendar names with their event counts in the next 30 days.

    Useful for finding which calendar actually holds work meetings -- names are
    not unique, so counts disambiguate.
    """
    script = f"""
set out to ""
tell application "Calendar"
    with timeout of 180 seconds
        set d1 to (current date) - (1 * days)
        set d2 to (current date) + (30 * days)
        repeat with i from 1 to count of calendars
            set c to calendar i
            set n to 0
            try
                set n to count of (every event of c whose start date > d1 and start date < d2)
            end try
            set out to out & (name of c) & "{FS}" & (n as string) & "{RS}"
        end repeat
    end timeout
end tell
return out
"""
    rows = parse_records(osa(script, timeout=200), 2)
    return {
        "calendars": [
            {"index": i + 1, "name": name, "events_next_30d": int(n)}
            for i, (name, n) in enumerate(rows)
        ]
    }


# --------------------------------------------------------------------------
# Mail
# --------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
def mail_folders() -> dict[str, Any]:
    """List Mail.app accounts and their mailbox (folder) names.

    Call this before `mail_search` to get exact folder names.
    """
    script = f"""
set out to ""
tell application "Mail"
    with timeout of 120 seconds
        repeat with i from 1 to count of accounts
            set a to account i
            set addrs to ""
            try
                -- Coercing a list to string uses text item delimiters, which
                -- default to "" and would fuse aliases into one garbage address.
                set save_delims to AppleScript's text item delimiters
                set AppleScript's text item delimiters to ", "
                set addrs to (email addresses of a) as string
                set AppleScript's text item delimiters to save_delims
            end try
            repeat with mb in (every mailbox of a)
                set out to out & (name of a) & "{FS}" & addrs & "{FS}" & (name of mb) & "{RS}"
            end repeat
        end repeat
    end timeout
end tell
return out
"""
    rows = parse_records(osa(script, timeout=140), 3)
    accounts: dict[str, dict[str, Any]] = {}
    for acct, addr, mbox in rows:
        entry = accounts.setdefault(acct, {"account": acct, "address": addr, "mailboxes": []})
        entry["mailboxes"].append(mbox)
    return {"accounts": list(accounts.values()), "configured_account": MAIL_ACCOUNT}


def _search_metadata(
    clause: str, limit: int, timeout: int
) -> tuple[list[list[str]], int]:
    """Run a metadata-only whose-clause search. Fast: no bodies fetched.

    Returns (rows, total_matched). `total_matched` is the size of the full match
    set before `limit` was applied, so callers can report truncation instead of
    passing a capped list off as exhaustive. `limit=0` means no cap.

    Rows come back in Mail's own collection order, which is NOT guaranteed to be
    newest-first; callers that care must sort.
    """
    cap = "" if limit <= 0 else f"\n        if n > {int(limit)} then set n to {int(limit)}"
    script = f"""
set out to ""
tell application "Mail"
    with timeout of {timeout} seconds
        set r to ({clause})
        set total to count of r
        set n to total{cap}
        repeat with i from 1 to n
            set m to item i of r
            set out to out & (id of m as string) & "{FS}" & my iso(date received of m) & "{FS}" & ¬
                my clean(sender of m) & "{FS}" & my clean(subject of m) & "{RS}"
        end repeat
        return (total as string) & "{RS}" & out
    end timeout
end tell
"""
    raw = osa(script, timeout=timeout + 20)
    head, _, rest = raw.partition(RS)
    try:
        total = int(head.strip())
    except ValueError:
        total = -1  # unknown; callers must not claim exhaustiveness
    return parse_records(rest, 4), total


@mcp.tool(annotations=READ_ONLY)
def mail_search(
    query: str,
    mailbox: str = "Inbox",
    field: str = "subject",
    days: int | None = None,
    limit: int = 25,
    max_scan: int = 60,
) -> dict[str, Any]:
    """Search mail and return message metadata.

    Performance matters here and the fields differ sharply:

      * `subject` / `sender` -- fast (sub-second on a few hundred messages).
      * `body` -- slow. Bodies are fetched one at a time, so this runs a
        two-stage search: narrow by date first, then match bodies in a bounded
        candidate set. Always pass `days` to keep it quick.

    Args:
        query: Substring to match (case-insensitive for `body`; Mail's own
            matching for `subject`/`sender`).
        mailbox: Folder name. "Inbox" means the unified inbox.
        field: One of "subject", "sender", "body".
        days: Only consider messages received in the last N days.
        limit: Max results returned.
        max_scan: Body search only -- how many of the most recent messages to
            pull bodies for (1-200, default 60). Raise it for wider coverage at
            roughly 0.13s per extra message; the response reports whether the
            scan was truncated.
    """
    field = field.lower().strip()
    if field not in {"subject", "sender", "body"}:
        return {"error": f"field must be subject, sender or body (got {field!r})"}
    if not query.strip():
        return {"error": "query must not be empty"}

    mb = mailbox_ref(mailbox)
    date_clause = ""
    if days is not None:
        date_clause = f" and date received > ((current date) - ({int(days)} * days))"

    started = time.monotonic()

    if field in {"subject", "sender"}:
        clause = f'messages of {mb} whose {field} contains "{esc(query)}"{date_clause}'
        rows, total = _search_metadata(clause, limit, timeout=90)
        results = [
            {"id": int(mid), "date": d, "sender": s, "subject": subj}
            for mid, d, s, subj in rows
        ]
        results.sort(key=lambda r: r["date"], reverse=True)
        out: dict[str, Any] = {
            "query": query,
            "field": field,
            "mailbox": mailbox,
            "count": len(results),
            "elapsed_s": round(time.monotonic() - started, 2),
            "results": results,
        }
        # A capped list must never read as an exhaustive one.
        if total > len(results):
            out["total_matched"] = total
            out["truncated"] = True
            out["note"] = (
                f"{total} messages match; {len(results)} returned (limit={limit}). "
                "Raise `limit` or narrow with `days` to see the rest."
            )
        elif total < 0:
            out["truncated"] = "unknown"
        return out

    # --- body search: two-stage ------------------------------------------
    # A single `whose content contains` over a few hundred messages takes
    # minutes and blows the tool timeout. Stage 1 is metadata-only (cheap),
    # stage 2 fetches bodies for a capped candidate set in ONE osascript call.
    if days is None:
        days = 7
        date_clause = " and date received > ((current date) - (7 * days))"

    # How many recent messages to pull bodies for. Measured ~0.13s/message on
    # locally-cached mail, so 200 is roughly a 30s ceiling. Tunable because the
    # right depth depends on the mailbox, not on anything we can detect here.
    cap = max(1, min(int(max_scan), 200))
    clause = f"messages of {mb} whose date received > ((current date) - ({int(days)} * days))"
    # Pull the whole window's metadata uncapped (cheap -- no bodies) and sort
    # newest-first here. Mail's collection order is not guaranteed to be
    # newest-first, so capping inside AppleScript could hand back the OLDEST
    # slice while the response claimed "most recent".
    all_cands, total_in_window = _search_metadata(clause, 0, timeout=90)
    all_cands.sort(key=lambda c: c[1], reverse=True)
    candidates = all_cands[:cap]

    if not candidates:
        return {
            "query": query,
            "field": "body",
            "mailbox": mailbox,
            "count": 0,
            "results": [],
            "note": f"No messages in the last {days} days in {mailbox}.",
        }

    ids = [c[0] for c in candidates]
    id_list = ", ".join(ids)
    # Bodies in one call: ~0.4s each measured, so scale the timeout by count.
    body_timeout = min(20 + int(len(ids) * 3), 240)
    script = f"""
set out to ""
tell application "Mail"
    with timeout of {body_timeout} seconds
        repeat with mid in {{{id_list}}}
            try
                set m to first message of {mb} whose id is mid
                set out to out & (mid as string) & "{FS}" & my clean(content of m) & "{RS}"
            end try
        end repeat
    end timeout
end tell
return out
"""
    body_rows = parse_records(osa(script, timeout=body_timeout + 30), 2)
    bodies = {mid: body for mid, body in body_rows}

    needle = query.lower()
    meta = {c[0]: c for c in candidates}
    results = []
    for mid in ids:
        body = bodies.get(mid, "")
        pos = body.lower().find(needle)
        if pos < 0:
            continue
        _, d, sender, subject = meta[mid]
        start = max(0, pos - 120)
        results.append(
            {
                "id": int(mid),
                "date": d,
                "sender": sender,
                "subject": subject,
                "snippet": body[start : pos + 240].replace("\n", " ").strip(),
            }
        )
        if len(results) >= limit:
            break

    out: dict[str, Any] = {
        "query": query,
        "field": "body",
        "mailbox": mailbox,
        "count": len(results),
        "elapsed_s": round(time.monotonic() - started, 2),
        "results": results,
    }
    # Never let a bounded scan look like an exhaustive one.
    if total_in_window > len(candidates):
        out["truncated"] = True
        out["messages_in_window"] = total_in_window
        out["note"] = (
            f"{total_in_window} messages fall in the last {days} days; only the "
            f"{len(candidates)} most recent were body-scanned (max_scan={cap}). "
            "Raise `max_scan`, narrow `days`, or search by subject for full coverage."
        )
    unavailable = len(ids) - len(bodies)
    if unavailable > 0:
        out["bodies_unavailable"] = unavailable
    # Some messages (calendar invites, image-only HTML) return an empty
    # `content`. They cannot be body-matched, so report them rather than letting
    # them look like genuine non-matches. Counted over messages we actually got
    # back, so they are not double-counted with `bodies_unavailable`.
    empty = sum(1 for body in bodies.values() if not body.strip())
    if empty:
        out["bodies_empty"] = empty
        out["bodies_empty_note"] = (
            f"{empty} of {len(bodies)} fetched message(s) have no extractable text "
            "body (typically meeting invites or image-only mail) and could not be "
            "body-matched. Search by subject to reach those."
        )
    out["candidates_scanned"] = len(ids)
    return out


@mcp.tool(annotations=READ_ONLY)
def mail_get(message_id: int, mailbox: str = "Inbox", max_chars: int = 20000) -> dict[str, Any]:
    """Fetch one message's full headers and body by id.

    Get ids from `mail_search`.
    """
    mb = mailbox_ref(mailbox)
    script = f"""
tell application "Mail"
    with timeout of 240 seconds
        set m to first message of {mb} whose id is {int(message_id)}
        return my clean(subject of m) & "{FS}" & my clean(sender of m) & "{FS}" & ¬
            my iso(date received of m) & "{FS}" & my clean(reply to of m) & "{FS}" & ¬
            my clean(content of m)
    end timeout
end tell
"""
    raw = osa(script, timeout=270)
    fields = raw.split(FS)
    if len(fields) < 5:
        return {"error": f"message {message_id} not found in {mailbox}"}
    subject, sender, date, reply_to, body = fields[0], fields[1], fields[2], fields[3], FS.join(fields[4:])
    return {
        "id": message_id,
        "mailbox": mailbox,
        "subject": subject,
        "sender": sender,
        "date": date,
        "reply_to": reply_to,
        "body": body[:max_chars],
        "body_chars": len(body),
        "body_truncated": len(body) > max_chars,
    }


# --------------------------------------------------------------------------
# SharePoint (OneDrive-synced libraries on disk)
# --------------------------------------------------------------------------

SKIP_DIRS = {".Trash", ".DS_Store"}
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".log", ".sql", ".py"}
OFFICE_SUFFIXES = {".xlsx", ".xlsm", ".docx", ".pptx"}

# Refuse to download and decode anything larger than this. Placeholders report
# their true size for free, so an oversized file costs nothing to reject.
MAX_READ_BYTES = 50 * 1024 * 1024


def sync_roots() -> list[Path]:
    """Synced SharePoint library roots.

    Globbed, never hardcoded: macOS names the current root
    "OneDrive-SharedLibraries-<Org> 2" (it appends " 2" after a name
    collision), and both the org name and that suffix can change.
    """
    base = Path.home() / "Library" / "CloudStorage"
    roots = sorted(p for p in base.glob("OneDrive-SharedLibraries-*") if p.is_dir())
    return roots


def _resolve_in_roots(raw_path: str) -> Path:
    """Resolve a caller-supplied path, refusing anything outside a sync root.

    This is a trust boundary -- the path arrives from the model -- so it gets a
    real containment check after symlink resolution, not a string prefix test.
    """
    roots = sync_roots()
    if not roots:
        raise ValueError(
            f"No synced SharePoint library found under {Path.home()}/Library/CloudStorage "
            "(looked for OneDrive-SharedLibraries-*). Sync a library in SharePoint first."
        )
    target = Path(raw_path).expanduser()
    if not target.is_absolute():
        target = roots[0] / raw_path
    target = target.resolve()
    for root in roots:
        if target == root.resolve() or target.is_relative_to(root.resolve()):
            return target
    raise ValueError(
        f"Path is outside the synced SharePoint roots: {target}. Allowed roots: "
        + ", ".join(str(r) for r in roots)
    )


def _is_hydrated(st: os.stat_result) -> bool:
    """True if file content is actually on disk.

    OneDrive Files-On-Demand placeholders report the logical size but occupy
    zero blocks -- verified: a downloaded xlsx showed blocks=40, an untouched
    one blocks=0. Checking this never triggers a download.

    A genuinely empty file also occupies zero blocks, so size 0 counts as
    local; otherwise every 0-byte file would be reported as needing a download.
    """
    if st.st_size == 0:
        return True
    return getattr(st, "st_blocks", 0) > 0


@mcp.tool(annotations=READ_ONLY)
def sp_roots() -> dict[str, Any]:
    """List synced SharePoint library roots and their top-level folders."""
    out = []
    for root in sync_roots():
        libs = []
        for child in sorted(root.iterdir()):
            if child.name.startswith(".") or child.name == "Icon\r":
                continue
            if child.is_dir():
                libs.append(child.name)
        out.append({"root": str(root), "libraries": libs})
    return {"roots": out}


@mcp.tool(annotations=READ_ONLY)
def sp_find(
    pattern: str,
    subdir: str | None = None,
    limit: int = 100,
    include_dirs: bool = False,
) -> dict[str, Any]:
    """Search synced SharePoint files by name or path. Downloads nothing.

    Matches filenames only -- no file contents are read, so this is instant and
    triggers no network traffic even across thousands of placeholder files. Use
    `sp_read` to pull the content of a specific hit.

    Args:
        pattern: Glob (e.g. "*.xlsx", "*backlog*") if it contains * or ?;
            otherwise a case-insensitive substring match on the name. Brackets
            are treated literally, so "Q3[2026]" matches that exact text.
        subdir: Restrict to a path under a sync root.
        limit: Max results.
        include_dirs: Also return matching directories.
    """
    roots = sync_roots()
    if not roots:
        return {"error": "No synced SharePoint libraries found under ~/Library/CloudStorage."}

    if subdir:
        try:
            search_bases = [_resolve_in_roots(subdir)]
        except ValueError as e:
            # Return the error shape the model can act on, not a traceback.
            return {"error": str(e)}
    else:
        search_bases = roots

    is_glob = any(ch in pattern for ch in "*?")
    needle = pattern.lower()

    hits: list[dict[str, Any]] = []
    scanned = 0

    # Collect ALL matches before sorting/truncating. Breaking at `limit` during
    # the walk would return an arbitrary filesystem-order slice that then gets
    # sorted by date -- presenting itself as "newest first" while omitting newer
    # files found later in the walk. Metadata-only, so a full walk is cheap.
    for base in search_bases:
        for path in base.rglob("*"):
            if any(part in SKIP_DIRS or part.startswith("._") for part in path.parts):
                continue
            is_dir = path.is_dir()
            if is_dir and not include_dirs:
                continue
            scanned += 1
            name = path.name
            matched = (
                fnmatch.fnmatch(name.lower(), needle) if is_glob else needle in name.lower()
            )
            if not matched:
                continue
            try:
                st = path.stat()  # metadata only -- no hydration
            except OSError:
                continue
            hits.append(
                {
                    "path": str(path),
                    "name": name,
                    "is_dir": is_dir,
                    "size": st.st_size,
                    "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
                    "hydrated": _is_hydrated(st),
                }
            )

    hits.sort(key=lambda h: h["modified"], reverse=True)
    total_matched = len(hits)
    truncated = total_matched > limit
    hits = hits[:limit]

    result: dict[str, Any] = {
        "pattern": pattern,
        "match_mode": "glob" if is_glob else "substring",
        "scanned": scanned,
        "count": len(hits),
        "results": hits,
    }
    if truncated:
        result["truncated"] = True
        result["total_matched"] = total_matched
        result["note"] = (
            f"{total_matched} files match; the {limit} most recently modified are "
            "returned. Raise `limit` or narrow `pattern`/`subdir`."
        )
    not_local = sum(1 for h in hits if not h["hydrated"] and not h["is_dir"])
    if not_local:
        result["placeholders"] = not_local
        result["placeholder_note"] = (
            f"{not_local} result(s) are not downloaded yet; sp_read will fetch them on demand."
        )
    return result


def _xml_text(data: bytes) -> str:
    """All text nodes of an XML document, whitespace-collapsed."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return ""
    return " ".join(t.strip() for t in root.itertext() if t and t.strip())


def _xlsx_sheet_parts(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """Ordered (sheet_name, zip_part) pairs for a workbook.

    Sheet names live in xl/workbook.xml in document order and point at parts by
    relationship id; the part filenames are NOT positionally meaningful. Pairing
    a lexicographic sort of `sheetN.xml` against document order mislabels
    everything past the ninth sheet (sheet1, sheet10, sheet11, sheet2, ...), so
    the relationship table is resolved properly here.
    """
    main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    pkg_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"

    parts = zf.namelist()

    rels: dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in parts:
        try:
            tree = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            for rel in tree.iter(f"{pkg_ns}Relationship"):
                rid, target = rel.get("Id"), rel.get("Target", "")
                if not rid or not target:
                    continue
                target = target.lstrip("/")
                if not target.startswith("xl/"):
                    target = "xl/" + target
                rels[rid] = target
        except ET.ParseError:
            pass

    ordered: list[tuple[str, str]] = []
    names: list[str] = []
    if "xl/workbook.xml" in parts:
        try:
            wb = ET.fromstring(zf.read("xl/workbook.xml"))
            for sheet in wb.iter(f"{main}sheet"):
                name = sheet.get("name", "")
                names.append(name)
                part = rels.get(sheet.get(f"{rel_ns}id", ""), "")
                if part in parts:
                    ordered.append((name, part))
        except ET.ParseError:
            pass
    if ordered:
        return ordered

    # No usable relationship table. Sort parts NUMERICALLY (so sheet2 precedes
    # sheet10 -- lexicographic order is what mislabels 10+ sheet workbooks).
    def sheet_no(p: str) -> int:
        m = re.search(r"sheet(\d+)\.xml$", p)
        return int(m.group(1)) if m else 0

    sheet_parts = sorted(
        (p for p in parts if re.match(r"xl/worksheets/sheet\d+\.xml$", p)), key=sheet_no
    )
    # If workbook.xml still gave us names, pair them positionally: numeric part
    # order matches document order in all but pathological files, and keeping
    # the real sheet names beats labelling everything "sheetN".
    if names and len(names) == len(sheet_parts):
        return list(zip(names, sheet_parts))
    return [(Path(p).stem, p) for p in sheet_parts]


def _xlsx_text(zf: zipfile.ZipFile, max_chars: int) -> str:
    """Row-wise text dump of a workbook.

    ponytail: resolves shared strings and emits tab-separated rows; ignores
    formatting, merged cells, formulas and number formats (a date shows as its
    serial number). Upgrade to openpyxl if real cell typing is needed.
    """
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    shared: list[str] = []
    if "xl/sharedStrings.xml" in zf.namelist():
        try:
            sst = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in sst.findall(f"{ns}si"):
                shared.append(" ".join(t.strip() for t in si.itertext() if t and t.strip()))
        except ET.ParseError:
            pass

    chunks: list[str] = []
    total = 0
    for label, sheet in _xlsx_sheet_parts(zf):
        chunks.append(f"### sheet: {label}")
        try:
            ws = ET.fromstring(zf.read(sheet))
        except (ET.ParseError, KeyError):
            continue
        for row in ws.iter(f"{ns}row"):
            cells = []
            for c in row.iter(f"{ns}c"):
                v = c.find(f"{ns}v")
                if c.get("t") == "s" and v is not None and v.text and v.text.isdigit():
                    i = int(v.text)
                    cells.append(shared[i] if i < len(shared) else "")
                elif c.get("t") == "inlineStr":
                    cells.append(" ".join(t.strip() for t in c.itertext() if t and t.strip()))
                elif v is not None and v.text:
                    cells.append(v.text)
                else:
                    cells.append("")
            line = "\t".join(cells).rstrip()
            if line:
                chunks.append(line)
                total += len(line)
                if total > max_chars:
                    chunks.append("... [truncated]")
                    return "\n".join(chunks)
    return "\n".join(chunks)


def _office_text(path: Path, max_chars: int) -> str:
    """Text from an OOXML file using only the stdlib (these are zip + XML)."""
    with zipfile.ZipFile(path) as zf:
        suffix = path.suffix.lower()
        if suffix in {".xlsx", ".xlsm"}:
            return _xlsx_text(zf, max_chars)
        if suffix == ".docx":
            return _xml_text(zf.read("word/document.xml"))
        if suffix == ".pptx":
            slides = sorted(
                n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)
            )
            parts = []
            for i, s in enumerate(slides, 1):
                parts.append(f"### slide {i}")
                parts.append(_xml_text(zf.read(s)))
            return "\n".join(parts)
    return ""


@mcp.tool(annotations=READ_ONLY)
def sp_read(path: str, max_chars: int = 20000) -> dict[str, Any]:
    """Read one synced SharePoint file's text content.

    This deliberately triggers a download if the file is a placeholder, so call
    it on specific files from `sp_find` rather than in a loop over many.

    Supports text formats directly and .xlsx/.docx/.pptx via stdlib extraction.
    PDFs are not supported.
    """
    try:
        target = _resolve_in_roots(path)
    except ValueError as e:
        return {"error": str(e)}

    if not target.exists():
        return {"error": f"Not found: {target}"}
    if target.is_dir():
        return {"error": f"Path is a directory, not a file: {target}"}

    st = target.stat()
    was_hydrated = _is_hydrated(st)
    suffix = target.suffix.lower()

    if suffix == ".pdf":
        return {
            "error": "PDF text extraction is not supported. Open the file directly.",
            "path": str(target),
        }

    # Size gate BEFORE reading. `max_chars` only trims after the whole file is
    # decoded, so without this a multi-gigabyte placeholder (.mp4, .zip, .pst)
    # would be fully downloaded from OneDrive and decoded into memory.
    if st.st_size > MAX_READ_BYTES:
        return {
            "error": (
                f"File is {st.st_size:,} bytes, over the {MAX_READ_BYTES:,}-byte read "
                "limit. Refusing to download and decode it. Open it directly instead."
            ),
            "path": str(target),
            "size": st.st_size,
            "was_downloaded_before": was_hydrated,
        }

    try:
        if suffix in OFFICE_SUFFIXES:
            text = _office_text(target, max_chars)
        else:
            # Known text suffixes, extensionless files, and anything else: try
            # text and let replacement chars reveal a binary.
            text = target.read_text(errors="replace")
    except zipfile.BadZipFile:
        return {"error": f"Not a readable OOXML file (corrupt or wrong extension): {target}"}
    except KeyError as e:
        # A valid zip missing the part we expect (odd producers, renamed archive).
        return {"error": f"Missing expected part {e} in {target.name}; not a usable OOXML file."}
    except OSError as e:
        return {
            "error": f"Read failed ({e}). If the file is a placeholder, OneDrive may be "
            "offline or the download was blocked."
        }

    # NOTE: deliberately no html.unescape here. Office text arrives already
    # decoded by ElementTree, so unescaping again would turn a cell literally
    # reading "&lt;tag&gt;" into "<tag>"; and for plain text/CSV/JSON it
    # corrupts ordinary content ("x=1&amp;y=2" -> "x=1&y=2").
    return {
        "path": str(target),
        "size": st.st_size,
        "was_downloaded_before": was_hydrated,
        "content": text[:max_chars],
        "chars": len(text),
        "truncated": len(text) > max_chars,
    }


if __name__ == "__main__":
    # stdio transport: stdout is the protocol channel, so diagnostics go to stderr.
    if not sync_roots():
        print(
            "warning: no OneDrive-SharedLibraries-* root found; SharePoint tools "
            "will return errors until a library is synced.",
            file=sys.stderr,
        )
    mcp.run()
