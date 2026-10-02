"""Command-line interface.

Human-readable output goes to stderr (Rich); machine-readable JSON goes to
stdout. When stdout is piped we emit JSON automatically so the tool composes
cleanly in scripts.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .auth import login as login_module
from .auth.session import Paths, clear_session, load_session
from .client import TeamsClient
from .errors import TeamsBrowserError
from .mcp import install as mcp_install
from .transcript_text import to_markdown, to_vtt

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Read-only Teams meetings, transcripts, chats, channel messages and shared files.",
)
mcp_app = typer.Typer(no_args_is_help=True, help="Manage the MCP server registration.")
app.add_typer(mcp_app, name="mcp")
console = Console(stderr=True)


def _json_mode(flag: bool) -> bool:
    return flag or not sys.stdout.isatty()


def _emit(data: Any, json_flag: bool) -> None:
    if _json_mode(json_flag):
        typer.echo(json.dumps({"ok": True, "schema_version": "1", "data": data}, default=str))
    else:
        console.print_json(json.dumps(data, default=str))


def _fail(exc: TeamsBrowserError, json_flag: bool = False) -> None:
    if _json_mode(json_flag):
        typer.echo(
            json.dumps(
                {"ok": False, "schema_version": "1", "error": str(exc), "type": type(exc).__name__}
            )
        )
    else:
        console.print(f"[red]error:[/red] {exc}")
    raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command()
def login(
    headless: bool = typer.Option(False, "--headless", help="Do not open a visible browser."),
    recon: bool = typer.Option(False, "--recon", help="Dump session contents after login."),
) -> None:
    """Authenticate by signing in to Teams in a browser window."""
    try:
        state = login_module.interactive_login(
            headed=not headless, on_wait=lambda m: console.print(f"[dim]{m}[/dim]")
        )
    except TeamsBrowserError as exc:
        _fail(exc)
        return
    console.print("[green]Signed in.[/green] Session captured.")
    if recon:
        _emit(login_module.recon_dump(state), json_flag=True)


@app.command()
def status(json_out: bool = typer.Option(False, "--json")) -> None:
    """Show the current session and token status."""
    try:
        with TeamsClient() as client:
            _emit(client.status(), json_out)
    except TeamsBrowserError as exc:
        _fail(exc, json_out)


@app.command()
def logout() -> None:
    """Delete the stored session."""
    clear_session(Paths.default())
    console.print("Session cleared.")


@app.command()
def refresh(json_out: bool = typer.Option(False, "--json")) -> None:
    """Refresh session tokens (browserless where possible)."""
    try:
        with TeamsClient() as client:
            method = client.refresh()
            _emit({"refreshed_via": method}, json_out)
    except TeamsBrowserError as exc:
        _fail(exc, json_out)


@app.command()
def recon(json_out: bool = typer.Option(False, "--json")) -> None:
    """Dump what the stored session contains (secrets redacted)."""
    state = load_session(Paths.default())
    if state is None:
        _fail(TeamsBrowserError("No session found. Run `teams-browser login` first."), json_out)
        return
    _emit(login_module.recon_dump(state), json_out)


@app.command()
def meetings(
    start: str | None = typer.Option(None, "--from", help="Start date (YYYY-MM-DD or today/yesterday)."),
    end: str | None = typer.Option(None, "--to", help="End date (YYYY-MM-DD)."),
    days: int | None = typer.Option(None, "--days", help="Look ahead N days (default 7)."),
    limit: int = typer.Option(50, "--limit"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List upcoming/recent Teams meetings."""
    try:
        start_dt = _parse_day(start) if start else None
        end_dt = _parse_day(end) if end else None
        if start_dt and not end_dt:
            end_dt = start_dt + timedelta(days=1)
        if end_dt is None and days:
            end_dt = (start_dt or datetime.now(tz=timezone.utc)) + timedelta(days=days)

        with TeamsClient() as client:
            result = client.list_meetings(start=start_dt, end=end_dt, limit=limit)
            _emit([m.model_dump(mode="json") for m in result], json_out)
            if not _json_mode(json_out):
                _render_meetings(result)
    except TeamsBrowserError as exc:
        _fail(exc, json_out)


@app.command()
def meeting(
    query: str = typer.Argument(..., help="Subject substring."),
    date_str: str | None = typer.Option(None, "--date", help="Restrict to a day (YYYY-MM-DD)."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show details for meetings matching a subject."""
    try:
        with TeamsClient() as client:
            matches = client.find_meetings(query, on_date=_parse_day(date_str) if date_str else None)
            _emit([m.model_dump(mode="json") for m in matches], json_out)
            if not _json_mode(json_out):
                _render_meetings(matches)
    except TeamsBrowserError as exc:
        _fail(exc, json_out)


@app.command()
def transcript(
    query: str | None = typer.Argument(None, help="Meeting subject substring, or a call participant."),
    date_str: str | None = typer.Option(None, "--date", help="Restrict to a day (YYYY-MM-DD)."),
    thread: str | None = typer.Option(
        None, "--thread", help="Fetch by thread id instead of subject (see `calls`)."
    ),
    fmt: str = typer.Option("text", "--format", help="text|md|vtt|json"),
    out: str | None = typer.Option(None, "--out", help="Write to a file instead of stdout."),
) -> None:
    """Fetch and clean the transcript for a meeting or call."""
    try:
        with TeamsClient() as client:
            day = _parse_day(date_str) if date_str else None
            if thread:
                result = client.get_transcript(thread, subject=query, meeting_date=day)
            elif query:
                result = client.get_transcript_for(query, on_date=day)
            else:
                raise TeamsBrowserError("Provide a subject or --thread.")
    except TeamsBrowserError as exc:
        _fail(exc)
        return

    if fmt == "text":
        rendered = result.text
    elif fmt == "md":
        rendered = to_markdown(result)
    elif fmt == "vtt":
        rendered = to_vtt(result.entries)
    elif fmt == "json":
        rendered = json.dumps(result.model_dump(mode="json"), indent=2)
    else:
        _fail(TeamsBrowserError(f"Unknown format: {fmt}"))
        return

    if out:
        from pathlib import Path

        Path(out).write_text(rendered, encoding="utf-8")
        console.print(f"Wrote {out}")
    else:
        typer.echo(rendered)


@app.command()
def chats(
    kind: str | None = typer.Option(
        None, "--kind", help="Filter: one_to_one|group|channel|meeting."
    ),
    limit: int = typer.Option(50, "--limit"),
    favorites: bool = typer.Option(False, "--favorites", help="Only favourites."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List your conversations: 1:1s, group chats, channels and meeting chats."""
    try:
        with TeamsClient() as client:
            conversations = client.list_conversations(top=limit)
    except TeamsBrowserError as exc:
        _fail(exc, json_out)
        return
    if kind:
        conversations = [c for c in conversations if c.kind == kind]
    if favorites:
        conversations = [c for c in conversations if c.is_favorite]
    _emit([c.model_dump(mode="json") for c in conversations], json_out)
    if not _json_mode(json_out):
        _render_conversations(conversations)


@app.command()
def calls(
    days: int = typer.Option(7, "--days", help="How far back to look (0 = all retained)."),
    participant: str | None = typer.Option(
        None, "--participant", help="Filter by participant name or MRI (e.g. Kevin)."
    ),
    with_transcript: bool | None = typer.Option(
        None,
        "--with-transcript/--without-transcript",
        help="Only calls that have, or lack, a transcript.",
    ),
    limit: int = typer.Option(50, "--limit"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List recent calls, including ad-hoc calls absent from the calendar."""
    until = datetime.now(tz=timezone.utc) + timedelta(days=1)
    since = until - timedelta(days=days) if days else None
    try:
        with TeamsClient() as client:
            items = client.list_calls(
                limit=100,
                since=since,
                until=until,
                participant=participant,
                has_transcript=with_transcript,
            )
    except TeamsBrowserError as exc:
        _fail(exc, json_out)
        return
    items = items[:limit]
    _emit([c.model_dump(mode="json") for c in items], json_out)
    if not _json_mode(json_out):
        _render_calls(items)


@app.command()
def messages(
    query: str = typer.Argument(..., help="Conversation topic substring or thread id (19:...)."),
    page_size: int = typer.Option(50, "--limit", help="Maximum messages to fetch."),
    system: bool = typer.Option(False, "--system", help="Include membership/call events."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Read messages from a chat or channel."""
    try:
        with TeamsClient() as client:
            conversation, items = client.get_messages_for(query, page_size=page_size)
    except TeamsBrowserError as exc:
        _fail(exc, json_out)
        return
    if not system:
        items = [m for m in items if not m.is_system]
    _emit(
        {
            "conversation": conversation.model_dump(mode="json"),
            "count": len(items),
            "messages": [m.model_dump(mode="json") for m in items],
        },
        json_out,
    )
    if not _json_mode(json_out):
        _render_messages(conversation, items)


@app.command()
def files(
    top: int = typer.Option(50, "--top"),
    recordings: bool = typer.Option(
        False, "--recordings", help="Include recording metadata (video is never downloaded)."
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List documents, images and links from your Teams working set."""
    try:
        with TeamsClient() as client:
            items = client.list_files(top=top, include_recordings=recordings)
    except TeamsBrowserError as exc:
        _fail(exc, json_out)
        return
    _emit([f.model_dump(mode="json") for f in items], json_out)
    if not _json_mode(json_out):
        _render_files(items)


@app.command()
def attachments(
    query: str = typer.Argument(..., help="Meeting subject substring."),
    date_str: str | None = typer.Option(None, "--date", help="Restrict to a day (YYYY-MM-DD)."),
    recordings: bool = typer.Option(False, "--recordings"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List files shared in a specific meeting."""
    try:
        with TeamsClient() as client:
            meeting = client.find_online_meeting(
                query, on_date=_parse_day(date_str) if date_str else None
            )
            if meeting is None:
                raise TeamsBrowserError(f"No online meeting matched '{query}'.")
            items = client.get_meeting_files(
                meeting.thread_id, include_recordings=recordings
            )
    except TeamsBrowserError as exc:
        _fail(exc, json_out)
        return
    _emit([f.model_dump(mode="json") for f in items], json_out)
    if not _json_mode(json_out):
        _render_files(items)


@app.command()
def sync(
    days_back: int = typer.Option(7, "--days-back", help="How far back to mirror."),
    days_forward: int = typer.Option(1, "--days-forward", help="How far ahead to mirror."),
    no_chats: bool = typer.Option(False, "--no-chats"),
    no_files: bool = typer.Option(False, "--no-files"),
    no_transcripts: bool = typer.Option(False, "--no-transcripts"),
    no_calls: bool = typer.Option(False, "--no-calls", help="Skip ad-hoc call transcripts."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Mirror meetings, transcripts, chats and files into the local archive."""
    try:
        with TeamsClient() as client:
            report = client.sync(
                days_back=days_back,
                days_forward=days_forward,
                include_chats=not no_chats,
                include_files=not no_files,
                include_transcripts=not no_transcripts,
                include_calls=not no_calls,
                log=None if _json_mode(json_out) else (lambda m: console.print(f"[dim]{m}[/dim]")),
            )
    except TeamsBrowserError as exc:
        _fail(exc, json_out)
        return
    _emit(report.model_dump(mode="json"), json_out)
    if not _json_mode(json_out):
        console.print(
            f"[green]Synced[/green] {report.meetings} meetings, "
            f"{report.transcripts} transcripts ({report.calls} calls seen), "
            f"{report.conversations} conversations, "
            f"{report.messages} messages, {report.files} files."
        )
        if report.errors:
            console.print(f"[yellow]{len(report.errors)} item(s) failed.[/yellow]")


@app.command()
def search(
    query: str = typer.Argument(..., help="Text to search for in the local archive."),
    source: str | None = typer.Option(
        None, "--source", help="Restrict: transcript|chat|meeting|file."
    ),
    limit: int = typer.Option(25, "--limit"),
    conversation: str | None = typer.Option(
        None, "--conversation", help="Restrict chat hits to one conversation id."
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Full-text search the local archive (run `sync` first)."""
    sources = [source] if source else None
    try:
        with TeamsClient() as client:
            hits = client.search(
                query, sources=sources, conversation_id=conversation, limit=limit
            )
    except TeamsBrowserError as exc:
        _fail(exc, json_out)
        return
    _emit([h.model_dump(mode="json") for h in hits], json_out)
    if not _json_mode(json_out):
        _render_hits(hits)


@app.command()
def digest(
    days: int = typer.Option(7, "--days", help="Window size in days."),
    out: str | None = typer.Option(None, "--out", help="Write markdown to a file."),
    include_declined: bool = typer.Option(
        False, "--include-declined", help="Include meetings you declined."
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Build a markdown digest of recent meetings and transcripts from the archive."""
    from .analytics import render_digest
    from .digest import build_digest

    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=days)
    try:
        with TeamsClient() as client:
            title, sections = build_digest(
                client.store, start=start, end=end, include_declined=include_declined
            )
    except TeamsBrowserError as exc:
        _fail(exc, json_out)
        return

    rendered = render_digest(title=title, sections=sections)
    summary = {
        "title": title,
        "meetings": len(sections),
        "transcripts": sum(1 for _, t, _a in sections if t),
    }

    if out:
        Path(out).write_text(rendered, encoding="utf-8")
        _emit({**summary, "saved_to": out}, json_out)
        if not _json_mode(json_out):
            console.print(f"Wrote {out}")
        return
    if _json_mode(json_out):
        _emit({**summary, "markdown": rendered}, json_out)
        return
    typer.echo(rendered)


@app.command()
def doctor(json_out: bool = typer.Option(False, "--json")) -> None:
    """Diagnose the session, tokens, endpoints and local archive."""
    checks: list[dict[str, Any]] = []

    def check(name: str, fn) -> None:
        try:
            detail = fn()
            checks.append({"check": name, "ok": True, "detail": detail})
        except Exception as exc:  # noqa: BLE001 - doctor reports everything
            checks.append({"check": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}"})

    state = load_session(Paths.default())
    checks.append(
        {
            "check": "session",
            "ok": state is not None,
            "detail": "found" if state else "missing - run `teams-browser login`",
        }
    )

    if state is not None:
        with TeamsClient() as client:
            check("region", lambda: client.region().region_partition)
            check("identity", lambda: client.identity()[1] or "unknown")
            check("meetings endpoint", lambda: f"{len(client.list_meetings(limit=1))} row(s)")
            check(
                "chats endpoint",
                lambda: f"{len(client.list_conversations(top=1))} conversation(s)",
            )
            check("files endpoint", lambda: f"{len(client.list_files(top=1))} file(s)")
            check("archive", lambda: client.store.stats())

    ok = all(c["ok"] for c in checks)
    _emit({"ok": ok, "checks": checks}, json_out)
    if not _json_mode(json_out):
        for c in checks:
            mark = "[green]ok[/green]" if c["ok"] else "[red]fail[/red]"
            console.print(f"{mark}  {c['check']}: {c['detail']}")


@mcp_app.command("clients")
def mcp_clients(json_out: bool = typer.Option(False, "--json")) -> None:
    """List supported MCP clients and their config paths."""
    targets = mcp_install.describe_clients()
    _emit(targets, json_out)
    if not _json_mode(json_out):
        for c in targets:
            console.print(f"[cyan]{c['client']}[/cyan]  {c['label']}\n    {c['path']}")


@mcp_app.command("install")
def mcp_install_cmd(
    client: str = typer.Argument(..., help="Client: " + ", ".join(mcp_install.CLIENTS)),
    path: str | None = typer.Option(None, "--path", help="Override the config file location."),
    print_only: bool = typer.Option(
        False, "--print", help="Print the snippet without writing anything."
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Register the teams-browser MCP server with an MCP client."""
    try:
        result = mcp_install.install(
            client,
            path=Path(path) if path else None,
            print_only=print_only,
        )
    except (ValueError, OSError) as exc:
        _fail(TeamsBrowserError(str(exc)), json_out)
        return

    if print_only:
        if _json_mode(json_out):
            _emit(result["config"], True)
        else:
            typer.echo(json.dumps(result["config"], indent=2))
        return

    _emit(result, json_out)
    if not _json_mode(json_out):
        console.print(f"[green]Registered[/green] teams-browser with {client} -> {result['path']}")
        console.print("[dim]Restart the client to pick up the new MCP server.[/dim]")


def _render_conversations(items: list) -> None:
    table = Table(show_lines=False)
    table.add_column("Kind", style="magenta", no_wrap=True)
    table.add_column("Topic")
    table.add_column("Last activity", style="cyan", no_wrap=True)
    table.add_column("Last message", style="dim", overflow="fold")
    for c in items:
        when = c.last_message_at.strftime("%d %b %H:%M") if c.last_message_at else "?"
        if c.topic:
            topic = c.topic
        elif c.kind == "one_to_one":
            topic = "direct chat"
        else:
            topic = c.id[:32]
        preview = (c.last_message_preview or "")[:80]
        if preview and c.last_sender:
            preview = f"{c.last_sender}: {preview}"
        table.add_row(c.kind, topic, when, preview)
    console.print(table)


def _render_calls(items: list) -> None:
    table = Table(show_lines=False)
    table.add_column("When", style="cyan", no_wrap=True)
    table.add_column("Direction", style="magenta", no_wrap=True)
    table.add_column("With")
    table.add_column("Transcript", justify="center")
    for c in items:
        when = c.start_time.strftime("%a %d %b %H:%M") if c.start_time else "?"
        names = [
            p.display_name
            for p in (c.originator, c.target)
            if p and p.display_name
        ]
        who = c.title or ", ".join(dict.fromkeys(names)) or c.call_id[:24]
        has_text = "yes" if (c.thread_id and c.has_transcript) else "-"
        table.add_row(when, c.direction or "-", who, has_text)
    console.print(table)


def _render_messages(conversation, items: list) -> None:
    console.print(
        f"[bold]{conversation.topic or conversation.id}[/bold] "
        f"[dim]({conversation.kind}, {len(items)} messages)[/dim]"
    )
    for m in items:
        when = m.timestamp.strftime("%d %b %H:%M") if m.timestamp else "?"
        console.print(f"[cyan]{when}[/cyan] [bold]{m.sender or 'System'}[/bold]: {m.text}")


def _render_files(items: list) -> None:
    table = Table(show_lines=False)
    table.add_column("Kind", style="magenta", no_wrap=True)
    table.add_column("Name", overflow="fold")
    table.add_column("Created", style="cyan", no_wrap=True)
    for f in items:
        when = f.created_at.strftime("%d %b %H:%M") if f.created_at else "?"
        name = f.name or f.url or f.id
        table.add_row(f.kind or f.extension or "-", name, when)
    console.print(table)


def _render_hits(items: list) -> None:
    if not items:
        console.print("[dim]No matches. Run `teams-browser sync` first.[/dim]")
        return
    for hit in items:
        when = hit.timestamp.strftime("%d %b %Y") if hit.timestamp else "?"
        console.print(
            f"[magenta]{hit.source}[/magenta] [dim]{when}[/dim] "
            f"[bold]{hit.title or hit.ref}[/bold]"
        )
        if hit.snippet:
            console.print(f"    {hit.snippet}")
        if hit.source == "chat" and hit.subtitle:
            console.print(f"    [dim]in {hit.subtitle}[/dim]")


def _render_meetings(items: list) -> None:
    table = Table(show_lines=False)
    table.add_column("When", style="cyan", no_wrap=True)
    table.add_column("Subject")
    table.add_column("Organizer", style="dim")
    table.add_column("Transcript", justify="center")
    for m in items:
        when = m.start_time.strftime("%a %d %b %H:%M") if m.start_time else "?"
        table.add_row(
            when,
            m.subject,
            m.organizer_name or "",
            "yes" if (m.is_online_meeting and m.thread_id) else "-",
        )
    console.print(table)


def _parse_day(value: str) -> datetime:
    lowered = value.strip().lower()
    today = datetime.now(tz=timezone.utc)
    if lowered == "today":
        return today.replace(hour=0, minute=0, second=0, microsecond=0)
    if lowered == "yesterday":
        return (today - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        parsed: date = date.fromisoformat(value)
    except ValueError as exc:
        raise TeamsBrowserError(f"Invalid date '{value}' (expected YYYY-MM-DD).") from exc
    return datetime.combine(parsed, datetime.min.time(), tzinfo=timezone.utc)


if __name__ == "__main__":
    app()