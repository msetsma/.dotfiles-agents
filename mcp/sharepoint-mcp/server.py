"""MCP server for SharePoint data on macOS.

Two data sources:

  * files -> OneDrive-synced SharePoint libraries on disk
  * lists -> SharePoint lists via Microsoft Graph, using the Azure CLI's
             already-signed-in delegated token (no app registration)

The file tools need no Microsoft Graph consent and no Full Disk Access. The
list tools do need Graph, reached through a signed-in `az` CLI. Reads are
always available; list *writes* are opt-in via SHAREPOINT_ENABLE_WRITES.
Everything is read-only by default: no tool writes files.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

# mcp SDK 2.0 renamed FastMCP to MCPServer; the decorator API is unchanged.
# Import path matches the existing house pattern in dev/kan-setup/bin/kan-mcp.py.
from mcp.server import MCPServer
from mcp.types import ToolAnnotations


mcp = MCPServer(
    name='sharepoint-mcp',
    version='0.1.0',
    instructions=(
        'Read-only access to SharePoint: OneDrive-synced SharePoint files and '
        'SharePoint lists (sp_lists/sp_list_items, via Graph -- needs a '
        'signed-in `az` CLI). List writes (sp_list_item_create/update/delete) '
        'exist only when SHAREPOINT_ENABLE_WRITES is set; they are destructive, '
        'so confirm with the user before calling them. '
        'sp_find never downloads anything; sp_read downloads on demand.'
    ),
)

# Every tool here is a read by default. Advertise that to clients so they can
# skip confirmation prompts and never treat a call as mutating.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)

# Write tools are opt-in (SHAREPOINT_ENABLE_WRITES). When enabled they carry honest
# hints so clients still prompt: create is non-destructive, update rewrites a
# value, delete is destructive.
WRITE_CREATE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)
WRITE_UPDATE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True)
WRITE_DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True)


# --------------------------------------------------------------------------
# SharePoint (OneDrive-synced libraries on disk)
# --------------------------------------------------------------------------

SKIP_DIRS = {'.Trash', '.DS_Store'}
TEXT_SUFFIXES = {'.txt', '.md', '.csv', '.tsv', '.json', '.xml', '.yaml', '.yml', '.log', '.sql', '.py'}
OFFICE_SUFFIXES = {'.xlsx', '.xlsm', '.docx', '.pptx'}

# Refuse to download and decode anything larger than this. Placeholders report
# their true size for free, so an oversized file costs nothing to reject.
MAX_READ_BYTES = 50 * 1024 * 1024


def sync_roots() -> list[Path]:
    """Synced SharePoint library roots.

    Globbed, never hardcoded: macOS names the current root
    "OneDrive-SharedLibraries-<Org> 2" (it appends " 2" after a name
    collision), and both the org name and that suffix can change.
    """
    base = Path.home() / 'Library' / 'CloudStorage'
    roots = sorted(p for p in base.glob('OneDrive-SharedLibraries-*') if p.is_dir())
    return roots


def _resolve_in_roots(raw_path: str) -> Path:
    """Resolve a caller-supplied path, refusing anything outside a sync root.

    This is a trust boundary -- the path arrives from the model -- so it gets a
    real containment check after symlink resolution, not a string prefix test.
    """
    roots = sync_roots()
    if not roots:
        raise ValueError(
            f'No synced SharePoint library found under {Path.home()}/Library/CloudStorage '
            '(looked for OneDrive-SharedLibraries-*). Sync a library in SharePoint first.'
        )
    target = Path(raw_path).expanduser()
    if not target.is_absolute():
        target = roots[0] / raw_path
    target = target.resolve()
    for root in roots:
        if target == root.resolve() or target.is_relative_to(root.resolve()):
            return target
    raise ValueError(
        f'Path is outside the synced SharePoint roots: {target}. Allowed roots: ' + ', '.join(str(r) for r in roots)
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
    return getattr(st, 'st_blocks', 0) > 0


@mcp.tool(annotations=READ_ONLY)
def sp_roots() -> dict[str, Any]:
    """List synced SharePoint library roots and their top-level folders."""
    out = []
    for root in sync_roots():
        libs = []
        for child in sorted(root.iterdir()):
            if child.name.startswith('.') or child.name == 'Icon\r':
                continue
            if child.is_dir():
                libs.append(child.name)
        out.append({'root': str(root), 'libraries': libs})
    return {'roots': out}


@mcp.tool(annotations=READ_ONLY)
def sp_find(pattern: str, subdir: str | None = None, limit: int = 100, include_dirs: bool = False) -> dict[str, Any]:
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
        return {'error': 'No synced SharePoint libraries found under ~/Library/CloudStorage.'}

    if subdir:
        try:
            search_bases = [_resolve_in_roots(subdir)]
        except ValueError as e:
            # Return the error shape the model can act on, not a traceback.
            return {'error': str(e)}
    else:
        search_bases = roots

    is_glob = any(ch in pattern for ch in '*?')
    needle = pattern.lower()

    hits: list[dict[str, Any]] = []
    scanned = 0

    # Collect ALL matches before sorting/truncating. Breaking at `limit` during
    # the walk would return an arbitrary filesystem-order slice that then gets
    # sorted by date -- presenting itself as "newest first" while omitting newer
    # files found later in the walk. Metadata-only, so a full walk is cheap.
    for base in search_bases:
        for path in base.rglob('*'):
            if any(part in SKIP_DIRS or part.startswith('._') for part in path.parts):
                continue
            is_dir = path.is_dir()
            if is_dir and not include_dirs:
                continue
            scanned += 1
            name = path.name
            matched = fnmatch.fnmatch(name.lower(), needle) if is_glob else needle in name.lower()
            if not matched:
                continue
            try:
                st = path.stat()  # metadata only -- no hydration
            except OSError:
                continue
            hits.append(
                {
                    'path': str(path),
                    'name': name,
                    'is_dir': is_dir,
                    'size': st.st_size,
                    'modified': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(st.st_mtime)),
                    'hydrated': _is_hydrated(st),
                }
            )

    hits.sort(key=lambda h: h['modified'], reverse=True)
    total_matched = len(hits)
    truncated = total_matched > limit
    hits = hits[:limit]

    result: dict[str, Any] = {
        'pattern': pattern,
        'match_mode': 'glob' if is_glob else 'substring',
        'scanned': scanned,
        'count': len(hits),
        'results': hits,
    }
    if truncated:
        result['truncated'] = True
        result['total_matched'] = total_matched
        result['note'] = (
            f'{total_matched} files match; the {limit} most recently modified are '
            'returned. Raise `limit` or narrow `pattern`/`subdir`.'
        )
    not_local = sum(1 for h in hits if not h['hydrated'] and not h['is_dir'])
    if not_local:
        result['placeholders'] = not_local
        result['placeholder_note'] = f'{not_local} result(s) are not downloaded yet; sp_read will fetch them on demand.'
    return result


def _xml_text(data: bytes) -> str:
    """All text nodes of an XML document, whitespace-collapsed."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return ''
    return ' '.join(t.strip() for t in root.itertext() if t and t.strip())


def _xlsx_sheet_parts(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """Ordered (sheet_name, zip_part) pairs for a workbook.

    Sheet names live in xl/workbook.xml in document order and point at parts by
    relationship id; the part filenames are NOT positionally meaningful. Pairing
    a lexicographic sort of `sheetN.xml` against document order mislabels
    everything past the ninth sheet (sheet1, sheet10, sheet11, sheet2, ...), so
    the relationship table is resolved properly here.
    """
    main = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    rel_ns = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
    pkg_ns = '{http://schemas.openxmlformats.org/package/2006/relationships}'

    parts = zf.namelist()

    rels: dict[str, str] = {}
    if 'xl/_rels/workbook.xml.rels' in parts:
        try:
            tree = ET.fromstring(zf.read('xl/_rels/workbook.xml.rels'))
            for rel in tree.iter(f'{pkg_ns}Relationship'):
                rid, target = rel.get('Id'), rel.get('Target', '')
                if not rid or not target:
                    continue
                target = target.lstrip('/')
                if not target.startswith('xl/'):
                    target = 'xl/' + target
                rels[rid] = target
        except ET.ParseError:
            pass

    ordered: list[tuple[str, str]] = []
    names: list[str] = []
    if 'xl/workbook.xml' in parts:
        try:
            wb = ET.fromstring(zf.read('xl/workbook.xml'))
            for sheet in wb.iter(f'{main}sheet'):
                name = sheet.get('name', '')
                names.append(name)
                part = rels.get(sheet.get(f'{rel_ns}id', ''), '')
                if part in parts:
                    ordered.append((name, part))
        except ET.ParseError:
            pass
    if ordered:
        return ordered

    # No usable relationship table. Sort parts NUMERICALLY (so sheet2 precedes
    # sheet10 -- lexicographic order is what mislabels 10+ sheet workbooks).
    def sheet_no(p: str) -> int:
        m = re.search(r'sheet(\d+)\.xml$', p)
        return int(m.group(1)) if m else 0

    sheet_parts = sorted((p for p in parts if re.match(r'xl/worksheets/sheet\d+\.xml$', p)), key=sheet_no)
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
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    shared: list[str] = []
    if 'xl/sharedStrings.xml' in zf.namelist():
        try:
            sst = ET.fromstring(zf.read('xl/sharedStrings.xml'))
            for si in sst.findall(f'{ns}si'):
                shared.append(' '.join(t.strip() for t in si.itertext() if t and t.strip()))
        except ET.ParseError:
            pass

    chunks: list[str] = []
    total = 0
    for label, sheet in _xlsx_sheet_parts(zf):
        chunks.append(f'### sheet: {label}')
        try:
            ws = ET.fromstring(zf.read(sheet))
        except (ET.ParseError, KeyError):
            continue
        for row in ws.iter(f'{ns}row'):
            cells = []
            for c in row.iter(f'{ns}c'):
                v = c.find(f'{ns}v')
                if c.get('t') == 's' and v is not None and v.text and v.text.isdigit():
                    i = int(v.text)
                    cells.append(shared[i] if i < len(shared) else '')
                elif c.get('t') == 'inlineStr':
                    cells.append(' '.join(t.strip() for t in c.itertext() if t and t.strip()))
                elif v is not None and v.text:
                    cells.append(v.text)
                else:
                    cells.append('')
            line = '\t'.join(cells).rstrip()
            if line:
                chunks.append(line)
                total += len(line)
                if total > max_chars:
                    chunks.append('... [truncated]')
                    return '\n'.join(chunks)
    return '\n'.join(chunks)


def _office_text(path: Path, max_chars: int) -> str:
    """Text from an OOXML file using only the stdlib (these are zip + XML)."""
    with zipfile.ZipFile(path) as zf:
        suffix = path.suffix.lower()
        if suffix in {'.xlsx', '.xlsm'}:
            return _xlsx_text(zf, max_chars)
        if suffix == '.docx':
            return _xml_text(zf.read('word/document.xml'))
        if suffix == '.pptx':
            slides = sorted(n for n in zf.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n))
            parts = []
            for i, s in enumerate(slides, 1):
                parts.append(f'### slide {i}')
                parts.append(_xml_text(zf.read(s)))
            return '\n'.join(parts)
    return ''


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
        return {'error': str(e)}

    if not target.exists():
        return {'error': f'Not found: {target}'}
    if target.is_dir():
        return {'error': f'Path is a directory, not a file: {target}'}

    st = target.stat()
    was_hydrated = _is_hydrated(st)
    suffix = target.suffix.lower()

    if suffix == '.pdf':
        return {'error': 'PDF text extraction is not supported. Open the file directly.', 'path': str(target)}

    # Size gate BEFORE reading. `max_chars` only trims after the whole file is
    # decoded, so without this a multi-gigabyte placeholder (.mp4, .zip, .pst)
    # would be fully downloaded from OneDrive and decoded into memory.
    if st.st_size > MAX_READ_BYTES:
        return {
            'error': (
                f'File is {st.st_size:,} bytes, over the {MAX_READ_BYTES:,}-byte read '
                'limit. Refusing to download and decode it. Open it directly instead.'
            ),
            'path': str(target),
            'size': st.st_size,
            'was_downloaded_before': was_hydrated,
        }

    try:
        if suffix in OFFICE_SUFFIXES:
            text = _office_text(target, max_chars)
        else:
            # Known text suffixes, extensionless files, and anything else: try
            # text and let replacement chars reveal a binary.
            text = target.read_text(errors='replace')
    except zipfile.BadZipFile:
        return {'error': f'Not a readable OOXML file (corrupt or wrong extension): {target}'}
    except KeyError as e:
        # A valid zip missing the part we expect (odd producers, renamed archive).
        return {'error': f'Missing expected part {e} in {target.name}; not a usable OOXML file.'}
    except OSError as e:
        return {
            'error': f'Read failed ({e}). If the file is a placeholder, OneDrive may be '
            'offline or the download was blocked.'
        }

    # NOTE: deliberately no html.unescape here. Office text arrives already
    # decoded by ElementTree, so unescaping again would turn a cell literally
    # reading "&lt;tag&gt;" into "<tag>"; and for plain text/CSV/JSON it
    # corrupts ordinary content ("x=1&amp;y=2" -> "x=1&y=2").
    return {
        'path': str(target),
        'size': st.st_size,
        'was_downloaded_before': was_hydrated,
        'content': text[:max_chars],
        'chars': len(text),
        'truncated': len(text) > max_chars,
    }


# --------------------------------------------------------------------------
# SharePoint lists (Microsoft Graph, via the Azure CLI's delegated token)
# --------------------------------------------------------------------------
#
# Unlike the file tools above, these talk to Graph. We borrow the Azure CLI's
# signed-in session (`az account get-access-token`), which in this tenant
# carries effective SharePoint read access without an app registration. The
# only new runtime dependency is the `az` CLI on PATH; if it is missing or
# signed out, each tool returns an {'error': ...} dict rather than raising.

GRAPH_ROOT = 'https://graph.microsoft.com/v1.0'


class GraphError(RuntimeError):
    pass


def _az_bin() -> str:
    """Absolute path to the Azure CLI, so a GUI-launched agent finds it.

    macOS GUI apps do not inherit the shell PATH, hence the explicit resolve
    rather than a bare 'az'.
    """
    path = shutil.which('az')
    if not path:
        raise GraphError('Azure CLI (`az`) not found on PATH. Install it and run `az login`.')
    return path


def _graph_request(
    url: str,
    method: str = 'GET',
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Call Graph through `az rest`, which supplies the CLI's token.

    Keeping HTTP in the CLI means token handling lives in one place instead of
    being re-implemented here. A 2xx with an empty body (e.g. DELETE's 204)
    returns {}.
    """
    cmd = [_az_bin(), 'rest', '--method', method, '--url', url, '--only-show-errors']
    hdrs = dict(headers or {})
    if body is not None:
        hdrs.setdefault('Content-Type', 'application/json')
    for key, value in hdrs.items():
        cmd += ['--headers', f'{key}={value}']
    if body is not None:
        cmd += ['--body', json.dumps(body)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise GraphError(f'Graph request timed out after {timeout}s: {url}') from None
    if proc.returncode != 0:
        raise GraphError(f'Graph request failed for {url}: ' + (proc.stderr or proc.stdout).strip())
    if not proc.stdout.strip():
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise GraphError(f'Graph returned unparseable output for {url}: ' + proc.stdout[:200]) from None


def _graph_get(url: str, headers: dict[str, str] | None = None, timeout: int = 60) -> dict[str, Any]:
    """GET a Graph URL (thin wrapper over `_graph_request`)."""
    return _graph_request(url, 'GET', None, headers, timeout)


def _site_path(site: str) -> str:
    """Graph path segment for a site URL, host:path form, or composite id.

    https://steelcase.sharepoint.com/sites/Foo -> steelcase.sharepoint.com:/sites/Foo
    A comma-composite id (host,guid,guid) passes through unchanged.
    """
    s = site.strip()
    if s.startswith('http'):
        rest = s.split('://', 1)[1].rstrip('/')
        host, _, path = rest.partition('/')
        return f'{host}:/{path}'
    return s


def _resolve_site_id(site: str) -> str:
    """Composite site id for any site input; ids already in that form pass through.

    List endpoints must use the composite id: the host:path form misparses once
    a nested OData expression ($expand=fields($select=...)) is added, and Graph
    then reports the property against `microsoft.graph.site`. Resolving first
    costs one extra GET but is the only form that handles nested expands.
    """
    if ',' in site:
        return site
    return _graph_get(f'{GRAPH_ROOT}/sites/{_site_path(site)}')['id']


@mcp.tool(annotations=READ_ONLY)
def sp_lists(site: str) -> dict[str, Any]:
    """List the lists and document libraries on a SharePoint site.

    Requires a signed-in Azure CLI (`az login`).

    Args:
        site: Site URL (e.g.
            https://steelcase.sharepoint.com/sites/UnifiedDataSciencePractice)
            or a Graph site id.
    """
    try:
        sid = _resolve_site_id(site)
        data = _graph_get(f'{GRAPH_ROOT}/sites/{sid}/lists?$select=id,name,displayName,list')
    except GraphError as e:
        return {'error': str(e)}
    lists = [
        {
            'id': item['id'],
            'name': item['name'],
            'display_name': item.get('displayName'),
            'template': (item.get('list') or {}).get('template'),
        }
        for item in data.get('value', [])
    ]
    return {'site_id': sid, 'count': len(lists), 'lists': lists}


@mcp.tool(annotations=READ_ONLY)
def sp_list_items(
    site: str, list_name: str, columns: list[str] | None = None, filter_query: str | None = None, limit: int = 200
) -> dict[str, Any]:
    """Read rows from a SharePoint list, with their column values. Read-only.

    Requires a signed-in Azure CLI (`az login`). Graph paging is followed
    automatically; at most `limit` rows come back. A non-indexed `filter_query`
    still works because the HonorNonIndexedQueries header is always sent.

    Args:
        site: Site URL or Graph site id.
        list_name: List display name or id.
        columns: Field names to return; omit for all columns.
        filter_query: OData clause on field values, e.g. "project_status eq 'In
            Progress'". A leading "fields/" is added if you omit it.
        limit: Maximum rows to return.
    """
    select = f'($select={",".join(columns)})' if columns else ''
    params = [f'$expand=fields{select}', '$top=200']
    if filter_query:
        clause = filter_query.strip()
        if not clause.startswith('fields/'):
            clause = 'fields/' + clause
        params.append('$filter=' + urllib.parse.quote(clause, safe="'/()"))

    rows: list[dict[str, Any]] = []
    truncated = False
    try:
        sid = _resolve_site_id(site)
        url = f'{GRAPH_ROOT}/sites/{sid}/lists/{urllib.parse.quote(list_name)}/items?{"&".join(params)}'
        while url:
            data = _graph_get(url, headers={'Prefer': 'HonorNonIndexedQueriesWarningMayFailRandomly'})
            for item in data.get('value', []):
                if len(rows) >= limit:
                    truncated = True
                    break
                rows.append({'id': item.get('id'), 'web_url': item.get('webUrl'), **item.get('fields', {})})
            if truncated:
                break
            url = data.get('@odata.nextLink')
    except GraphError as e:
        return {'error': str(e)}

    out: dict[str, Any] = {'count': len(rows), 'items': rows}
    if truncated:
        out['truncated'] = True
        out['note'] = f'More than {limit} rows exist; raise `limit` to see the rest.'
    return out


# --------------------------------------------------------------------------
# SharePoint list writes (opt-in via SHAREPOINT_ENABLE_WRITES)
# --------------------------------------------------------------------------
#
# Registered only when SHAREPOINT_ENABLE_WRITES is set, so the default install
# advertises reads alone. The functions are defined unconditionally so they
# stay unit-testable without flipping the flag.

ENABLE_WRITES = os.environ.get('SHAREPOINT_ENABLE_WRITES', '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _list_items_url(site_id: str, list_name: str, suffix: str = '') -> str:
    return f'{GRAPH_ROOT}/sites/{site_id}/lists/{urllib.parse.quote(list_name)}/items{suffix}'


def sp_list_item_create(site: str, list_name: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Create an item in a SharePoint list (available only with SHAREPOINT_ENABLE_WRITES).

    Args:
        site: Site URL or Graph site id.
        list_name: List display name or id.
        fields: Column values for the new item, e.g. {"Title": "New item"}.
    """
    if not fields:
        return {'error': 'fields must not be empty'}
    try:
        sid = _resolve_site_id(site)
        data = _graph_request(_list_items_url(sid, list_name), 'POST', {'fields': fields})
    except GraphError as e:
        return {'error': str(e)}
    return {'id': data.get('id'), 'web_url': data.get('webUrl')}


def sp_list_item_update(site: str, list_name: str, item_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Update fields on a list item; keys you omit are left untouched (opt-in).

    Args:
        site: Site URL or Graph site id.
        list_name: List display name or id.
        item_id: Item id, as returned by `sp_list_items`.
        fields: Column values to change.
    """
    if not fields:
        return {'error': 'fields must not be empty'}
    try:
        sid = _resolve_site_id(site)
        url = _list_items_url(sid, list_name, f'/{urllib.parse.quote(str(item_id))}/fields')
        _graph_request(url, 'PATCH', fields)
    except GraphError as e:
        return {'error': str(e)}
    return {'id': str(item_id), 'updated': sorted(fields)}


def sp_list_item_delete(site: str, list_name: str, item_id: str) -> dict[str, Any]:
    """Delete a list item, sending it to the site recycle bin (opt-in, destructive).

    Args:
        site: Site URL or Graph site id.
        list_name: List display name or id.
        item_id: Item id, as returned by `sp_list_items`.
    """
    try:
        sid = _resolve_site_id(site)
        url = _list_items_url(sid, list_name, f'/{urllib.parse.quote(str(item_id))}')
        _graph_request(url, 'DELETE')
    except GraphError as e:
        return {'error': str(e)}
    return {'deleted': True, 'item_id': str(item_id)}


WRITE_TOOLS = (
    (sp_list_item_create, WRITE_CREATE),
    (sp_list_item_update, WRITE_UPDATE),
    (sp_list_item_delete, WRITE_DELETE),
)

REGISTERED_WRITE_TOOLS: list[str] = []
if ENABLE_WRITES:
    for _write_tool, _write_annotations in WRITE_TOOLS:
        mcp.tool(annotations=_write_annotations)(_write_tool)
        REGISTERED_WRITE_TOOLS.append(_write_tool.__name__)


if __name__ == '__main__':
    # stdio transport: stdout is the protocol channel, so diagnostics go to stderr.
    if not sync_roots():
        print(
            'warning: no OneDrive-SharedLibraries-* root found; SharePoint tools '
            'will return errors until a library is synced.',
            file=sys.stderr,
        )
    mcp.run()
