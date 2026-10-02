# m365-local-mcp

Read-only MCP server exposing local M365 data on macOS: Outlook meetings, mail
search, and OneDrive-synced SharePoint files.

Needs **no Microsoft Graph consent and no Full Disk Access**.

## Why this exists

The normal programmatic routes to this data are all closed on this machine:

- **Graph is scope-blocked.** An `az` token authenticates (`/me` works), but
  `/me/messages` returns `ErrorAccessDenied` and `/sites?search=*` returns
  `accessDenied`. The Azure CLI app registration has no Mail.Read / Sites.Read
  consented in the tenant. That's an IT request, not a code fix.
- **Outlook's store is opaque.** New Outlook 16.112 keeps mail in a proprietary
  Hx binary (`HxStore.hxd`). The legacy `Outlook.sqlite` has zero mail rows
  because `AllAccountsMigratedFromLegacy = 1`. Outlook's own AppleScript
  interface reports 0 accounts, 0 messages, 0 events.
- **Raw mail/calendar files are TCC-protected.** `~/Library/Mail`,
  `~/Library/Calendars` and `~/Library/Accounts` all return
  "Operation not permitted" without Full Disk Access.

What *does* work: Mail.app and Calendar.app already sync the Exchange account
and both answer AppleScript, and SharePoint libraries synced through OneDrive
are ordinary files on disk. This server wraps those three routes.

## Install

```bash
cd ~/.agentdots/mcp/m365-local-mcp
uv run test_server.py          # offline self-check, no Mail/Calendar needed
claude mcp add --scope user m365-local -- \
    uv run --directory ~/.agentdots/mcp/m365-local-mcp server.py
```

Requires an Exchange account in Mail.app and at least one synced SharePoint
library. To sync a library: open it in SharePoint, then **Add shortcut to
OneDrive** (or **Sync**).

`M365_MAIL_ACCOUNT` overrides the Mail.app account name (default `Exchange`).

## Tools

| Tool | What it does |
|---|---|
| `meetings_list` | Events in a date window, with attendees on request |
| `calendars_list` | Calendar names + event counts (names are not unique) |
| `mail_folders` | Accounts and their 24 mailbox names |
| `mail_search` | Search by `subject`, `sender`, or `body` |
| `mail_get` | Full headers + body for one message id |
| `sp_roots` | Synced library roots |
| `sp_find` | Filename search. **Downloads nothing.** |
| `sp_read` | Text of one file. Downloads it if needed. |

All tools are annotated `read_only_hint=True`. There is no tool that sends
mail, creates events, or writes files.

## Performance, measured

| Operation | Cost |
|---|---|
| `mail_search` subject/sender | ~0.6s (11 hits over 338 messages) |
| `mail_search` body | ~2.7s for 13 candidates; scales ~0.13s/message |
| `sp_find` over 1010 files | instant, zero network |
| `meetings_list` 8-day window | ~2s |

Body search is the one slow path. A naive `whose content contains` over the
inbox measured **2 minutes**, so body search runs two-stage instead: narrow by
date via metadata (cheap), then pull bodies for a capped candidate set
(`max_scan`, default 60) and match in Python.

Every capped path reports it. `mail_search` returns `truncated` plus
`total_matched`, and `sp_find` returns `total_matched` alongside the newest-first
slice it kept. Candidates are sorted by date in Python before capping, because
Mail's collection order is not guaranteed newest-first — capping inside
AppleScript could hand back the oldest slice while claiming "most recent".

## Known limitations

These are properties of the underlying APIs, not bugs to fix here.

- **Recurring events report their series start date.** Calendar.app matches
  recurring events by occurrence but exposes only the series' first start, so a
  weekly meeting begun in January shows a January date inside an August window.
  Such events are flagged `recurring_series: true` rather than given a
  misleading date. Reading the `recurrence` rule to resolve the real occurrence
  timed out past 5 minutes on a single calendar, so it isn't attempted.
- **Some messages have no body text.** Meeting invites and image-only HTML mail
  expose an empty `content` and cannot be body-matched. Counted and reported as
  `bodies_empty`; reach them via subject search.
- **Excel dates come out as serial numbers.** `sp_read` uses stdlib-only OOXML
  extraction, so a date cell reads `45378.51` rather than a date. Formatting,
  merged cells and formulas are ignored. Upgrade to `openpyxl` if that matters.
- **`sp_read` refuses files over 50 MB** (`MAX_READ_BYTES`). Placeholders report
  their true size for free, so an oversized video or archive is rejected without
  downloading it.
- **File content is never HTML-unescaped.** Text arrives verbatim, so a CSV
  containing `x=1&amp;y=2` reads back exactly that. Office text is already
  entity-decoded by the XML parser; unescaping again would corrupt cells whose
  literal content looks like markup.
- **OneDrive may hydrate a library on its own.** Observed here: the whole
  1010-file library went from 32 KB to 12.4 GB on disk in one bulk event hours
  after being synced, with no `sp_read` involved. `sp_find` is not the cause --
  it only ever calls `stat()`. To reclaim the space, right-click the folder in
  Finder and choose **Free up space**.
- **PDFs are not extracted.** `sp_read` refuses them explicitly.
- **Second mailbox missing.** Outlook has `mlops@example.com`; Mail.app
  carries only `you@example.com`. Add it in Mail to make it visible.
- **`sp_read` costs network.** Files are Files-On-Demand placeholders
  (`st_blocks == 0`). Reading hydrates them. Call it on specific hits from
  `sp_find`, not in a loop over hundreds.
- **Mail.app and Calendar.app must be running** for AppleScript to answer.
- **First call may prompt for Automation access.** Approve it; a denial
  surfaces as a clear instruction rather than a traceback.

## Verifying against live data

```bash
uv run test_server.py     # 11 offline asserts

uv run python -c "
import server as s
print(s.meetings_list(days_ahead=7)['count'], 'events')
print(s.mail_search('GitHub')['count'], 'subject hits')
r = s.sp_find('*.xlsx', limit=200)
print(r['scanned'], 'files scanned,', r['count'], 'hits,', r['placeholders'], 'not downloaded')
"
```

The load-bearing check for SharePoint: record `du -sk` on the sync root, run
`sp_find`, and confirm the number is **unchanged**. That proves search isn't
quietly pulling the library down.
