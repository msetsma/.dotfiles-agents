# teams-browser

Read-only access to Microsoft Teams **meetings**, **transcripts**, **calls**
(including ad-hoc and 1:1 calls), **chats**, **channel messages** and **shared
files** using the tokens your browser already holds — no Microsoft Graph app
registration, no admin consent, no write scopes.

> Personal workaround for environments where the organisation has not yet
> enabled a Graph/MCP integration for Teams. When enterprise Graph read access
> lands, the same CLI/MCP surface can be backed by a Graph provider
> (`src/teams_browser/providers/`).

## How it works

The Teams web client authenticates with Entra ID and stores bearer tokens in
`localStorage` (classic MSAL entries and new-Teams `tmp.auth.v1.*` encrypted
entries) plus auth cookies. `teams-browser` captures that session once via
Playwright, then calls the **same internal APIs the web client uses**:

| Capability | API |
| --- | --- |
| List meetings | `GET {teams}/api/mt[/part/{region-partition}]/v2.1/me/calendars/calendarView` |
| Chats & channels | `GET {teams}/api/chatsvc/{region}/v1/users/ME/conversations` (`Authentication: skypetoken=…`) |
| Call history | `GET {teams}/api/chatsvc/{region}/v1/users/ME/conversations/48:calllogs/messages` (`call-log` entries joined to `Media_CallLog*` events on `CallId`) |
| Messages | `GET {teams}/api/chatsvc/{region}/v1/users/ME/conversations/{id}/messages` |
| Transcript | `GET {substrate}/api/beta/me/WorkingSetFiles/?$filter=…MeetingThreadId eq '…'` → embedded `TranscriptJson` |
| Shared files | `GET {substrate}/api/beta/me/WorkingSetFiles/?$select=Visualization,…` |

Channel threads are ordinary `chatsvc` conversations (`@thread.tacv2`), so one
auth path covers 1:1s, group chats, channels and meeting chats. Ad-hoc calls
(a 1:1 "call with", or a call started from a chat) have no calendar entry, so
they are discovered from the `48:calllogs` conversation, whose
`RichText/Media_CallLog*` events carry the `ThreadId` the transcript filter
needs. No DOM scraping: the browser is only used to establish a session.

## Install

```sh
uv sync --extra mcp          # core + MCP server
uv run playwright install chromium
```

## Usage

```sh
uv run teams-browser login                 # one-time interactive sign-in
uv run teams-browser doctor                # verify session, tokens, endpoints
uv run teams-browser meetings --days 7     # upcoming meetings
uv run teams-browser transcript "Design Review" --date 2026-09-15 --format md --out review.md
uv run teams-browser calls --participant Kevin   # ad-hoc calls (no calendar entry)
uv run teams-browser transcript --thread 19:...@unq.gbl.spaces   # transcript by id
uv run teams-browser chats --kind channel  # conversations
uv run teams-browser messages "Data Platform"
uv run teams-browser files --top 20        # documents, images and links
uv run teams-browser attachments "Design Review"
uv run teams-browser sync --days-back 30   # mirror into the local archive
uv run teams-browser search roadmap        # full-text search the archive
uv run teams-browser digest --days 7 --out weekly.md
```

`--json` (or piping stdout) emits `{ok, schema_version, data}` to stdout; human
tables go to stderr.

### CLI commands

| Command | Purpose |
| --- | --- |
| `login [--headless] [--recon]` | Authenticate in a browser and capture the session |
| `status` / `refresh` / `logout` | Session identity, tokens, teardown |
| `doctor` | Probe session, region, identity, each endpoint and the archive |
| `recon` | Dump localStorage/token inventory (secrets redacted) |
| `meetings [--from --to --days --limit]` | List meetings |
| `meeting <subject> [--date]` | Meeting details |
| `transcript <subject> [--date --thread --format text\|md\|vtt\|json --out]` | Transcript (calendar meeting or ad-hoc call) |
| `chats [--kind --favorites --limit]` | List 1:1s, group chats, channels, meeting chats |
| `calls [--days --participant --with-transcript --limit]` | Recent calls, including ad-hoc/1:1 calls absent from the calendar |
| `messages <topic\|thread-id> [--limit --system]` | Read a chat or channel |
| `files [--top --recordings]` | Documents, images and links from your working set |
| `attachments <subject> [--date]` | Files shared in a specific meeting |
| `sync [--days-back --days-forward --no-chats --no-files --no-transcripts --no-calls]` | Mirror into the local archive |
| `search <query> [--source transcript\|chat\|meeting\|file --limit]` | Full-text search the archive |
| `digest [--days --out --include-declined]` | Markdown digest of recent meetings |
| `mcp install <client>` / `mcp clients` | Register the MCP server with a client |

## The local archive

Transcripts and recordings expire on tenant retention, and querying the internal
APIs on every question is slow. `sync` mirrors meetings, transcripts, chats and
files into a local SQLite database (`~/.cache/teams-browser/archive.db`) with an
FTS5 index over transcript and message text.

```sh
uv run teams-browser sync --days-back 30
uv run teams-browser search "freight planning" --source transcript
```

Everything after `sync` is local: `search` and `digest` never touch the network.
Run `sync` from cron/launchd if you want history kept ahead of retention. Per-item
failures are collected in the report rather than aborting the run.

## MCP server

```sh
uv run teams-browser-mcp          # run the stdio server directly
uv run teams-browser mcp clients  # list supported clients + config paths
uv run teams-browser mcp install claude-desktop
```

`mcp install` writes the correct entry for the client (merging into any existing
config) and uses an absolute path to the installed server, so it works from any
directory. Supported: `claude-desktop`, `claude-code`, `cursor`, `vscode`,
`opencode`. Use `--print` to see the snippet without writing, or `--path` to
target a specific file.

**Tools** — annotated so clients can skip confirmation on the read-only ones:

| Tool | Notes |
| --- | --- |
| `list_meetings`, `get_meeting` | Calendar |
| `get_transcript`, `get_transcript_analytics` | Prose transcript; talk-time stats. By subject (meetings or call participant) or `thread_id` |
| `list_calls` | Recent calls incl. ad-hoc/1:1; returns thread ids for transcripts |
| `get_meeting_attachments` | Documents/links shared in a meeting (no recordings) |
| `list_chats`, `get_chat_messages` | 1:1s, group chats, channels |
| `search_archive`, `get_archive_digest` | Local only, no network |
| `sync_archive`, `save_transcript` | Write to local disk only |
| `session_status` | Auth/token health |
| `start_login` | Opens a browser window for interactive sign-in (returns immediately) |

**Resources** — attachable without a tool round-trip: `teams://meetings/today`,
`teams://meetings/{day}`, `teams://archive/stats`,
`teams://archive/transcripts`, `teams://archive/chats`.

**Prompts**: `summarise_meeting`, `weekly_digest`, `find_decisions`.

## Architecture

```
auth/       login (Playwright) · session (encrypted storage) · tokens · refresh
api/        http (retry/backoff) · calendar · chats · calls · files · transcript · util
analytics   talk time, slicing, merge, digest rendering (local, no network)
store       SQLite + FTS5 archive
sync        incremental mirror of meetings/transcripts/chats/files
client      TeamsClient facade shared by CLI and MCP
mcp/        FastMCP stdio server (tools, resources, prompts)
scripts/    recon.py — probe the internal endpoints when something drifts
```

Session state is encrypted at rest (OS keychain key, `0600` fallback).

### Token refresh

Tokens expire roughly hourly. `TeamsClient.ensure_valid()` refreshes
automatically: first via the Entra refresh-token grant (fast, browserless),
falling back to a headless browser pass if the refresh token is gone. If
Conditional Access blocks headless refresh, re-run `teams-browser login`.
The MCP server skips the browser fallback (a tool call cannot wait ~60s on a
browser without tripping the client's timeout) and instead fails fast with an
instruction to re-login; the `start_login` tool lets the client kick off an
interactive sign-in window on the spot.

## Caveats

- These are **undocumented internal APIs**. They are read-only and run entirely
  in the signed-in user's context, but they can change without notice and may
  be subject to tenant DLP/monitoring policy. Check with your security team
  before rolling this out beyond personal use.
- **Recordings are never downloaded.** Video is listed as metadata at most
  (`files --recordings`); attachments exclude it by default.
- Meeting attachments only appear if the file carries the meeting's thread id,
  so `files` is usually the more productive way to find shared material.
- Transcripts require transcription to have been enabled and may only be
  discoverable for a limited window — hence `sync`. Ad-hoc/1:1 call transcripts
  are matched via the `48:calllogs` chat; a call only appears there once Teams
  has registered the recording/transcript event.
- These private endpoints will drift. `tests/fixtures/` + `test_contracts.py`
  pin the fields we rely on, and `scripts/recon.py` re-probes live shapes.
- A live smoke test can be run with `TEAMS_BROWSER_LIVE=1` after `login`.
