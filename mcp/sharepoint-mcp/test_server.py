"""Self-check for sharepoint-mcp. Runs offline: no network, no SharePoint.

uv run test_server.py
"""

import contextlib
import io
import os
import tempfile
import zipfile
from pathlib import Path

import server as s


def test_path_escape_rejected():
    """Trust boundary: model-supplied paths must not escape the sync roots."""
    if not s.sync_roots():
        print('  skip: no sync root present')
        return
    for bad in ['../../etc/passwd', '/etc/passwd', '~/.ssh/id_rsa']:
        try:
            s._resolve_in_roots(bad)
        except ValueError:
            continue
        raise AssertionError(f'escape not blocked: {bad}')


def test_path_inside_root_allowed():
    roots = s.sync_roots()
    if not roots:
        print('  skip: no sync root present')
        return
    assert s._resolve_in_roots(str(roots[0])) == roots[0].resolve()


def test_hydrated_flag():
    """blocks==0 means placeholder; checking must not download."""

    class FakeStat:
        st_size = 21355
        st_blocks = 0

    class RealStat:
        st_size = 17149
        st_blocks = 40

    assert s._is_hydrated(FakeStat()) is False
    assert s._is_hydrated(RealStat()) is True


def _synthetic_xlsx() -> Path:
    """Minimal OOXML workbook: one shared string, one numeric cell."""
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('xl/sharedStrings.xml', f'<sst xmlns="{ns}"><si><t>Viewer</t></si><si><t>Team</t></si></sst>')
        zf.writestr('xl/workbook.xml', f'<workbook xmlns="{ns}"><sheets><sheet name="Report"/></sheets></workbook>')
        zf.writestr(
            'xl/worksheets/sheet1.xml',
            f'<worksheet xmlns="{ns}"><sheetData>'
            '<row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>'
            '<row><c><v>42</v></c><c t="inlineStr"><is><t>inline</t></is></c></row>'
            '</sheetData></worksheet>',
        )
    out = Path('/tmp/_sp_test.xlsx')
    out.write_bytes(buf.getvalue())
    return out


def test_xlsx_extraction():
    p = _synthetic_xlsx()
    try:
        with zipfile.ZipFile(p) as zf:
            text = s._xlsx_text(zf, max_chars=10_000)
    finally:
        p.unlink(missing_ok=True)
    assert '### sheet: Report' in text, text
    assert 'Viewer\tTeam' in text, text  # shared strings resolved by index
    assert '42' in text, text  # numeric cell
    assert 'inline' in text, text  # inlineStr cell


def _multi_sheet_xlsx(n: int) -> Path:
    """Workbook with n sheets wired through a relationship table.

    Part filenames are emitted in an order that does NOT match document order,
    which is what breaks naive lexicographic pairing at 10+ sheets.
    """
    main = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    pkg = 'http://schemas.openxmlformats.org/package/2006/relationships'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        sheets = ''.join(f'<sheet name="S{i}" r:id="rId{i}"/>' for i in range(1, n + 1))
        zf.writestr('xl/workbook.xml', f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>{sheets}</sheets></workbook>')
        rels = ''.join(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>' for i in range(1, n + 1))
        zf.writestr('xl/_rels/workbook.xml.rels', f'<Relationships xmlns="{pkg}">{rels}</Relationships>')
        for i in range(1, n + 1):
            zf.writestr(
                f'xl/worksheets/sheet{i}.xml',
                f'<worksheet xmlns="{main}"><sheetData><row>'
                f'<c t="inlineStr"><is><t>MARKER{i}</t></is></c>'
                '</row></sheetData></worksheet>',
            )
    out = Path('/tmp/_sp_multi.xlsx')
    out.write_bytes(buf.getvalue())
    return out


def test_xlsx_sheet_labels_match_content():
    """Label N must sit above MARKER N, even past 9 sheets."""
    p = _multi_sheet_xlsx(11)
    try:
        with zipfile.ZipFile(p) as zf:
            text = s._xlsx_text(zf, max_chars=100_000)
    finally:
        p.unlink(missing_ok=True)
    for i in range(1, 12):
        block = text.split(f'### sheet: S{i}\n')
        assert len(block) == 2, f'missing label S{i}'
        assert block[1].startswith(f'MARKER{i}'), f'S{i} labelled over wrong content: {block[1][:24]!r}'


def test_xlsx_fallback_sorts_numerically():
    """Without a rel table, sheet2 must precede sheet10."""
    main = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for i in (1, 2, 10):
            zf.writestr(f'xl/worksheets/sheet{i}.xml', f'<worksheet xmlns="{main}"/>')
    p = Path('/tmp/_sp_fallback.xlsx')
    p.write_bytes(buf.getvalue())
    try:
        with zipfile.ZipFile(p) as zf:
            order = [name for name, _ in s._xlsx_sheet_parts(zf)]
    finally:
        p.unlink(missing_ok=True)
    assert order == ['sheet1', 'sheet2', 'sheet10'], order


@contextlib.contextmanager
def fake_root():
    """A throwaway sync root.

    Never write into the real synced library -- anything created there uploads
    straight to corporate SharePoint.
    """
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / 'OneDrive-SharedLibraries-Test'
        root.mkdir()
        original = s.sync_roots
        s.sync_roots = lambda: [root]
        try:
            yield root
        finally:
            s.sync_roots = original


def test_no_html_unescaping_of_file_content():
    """Ampersand entities in plain text must survive verbatim."""
    with fake_root() as root:
        f = root / 'amp.csv'
        f.write_text('a,b\nx=1&amp;y=2,&amp;amp;\n')
        r = s.sp_read(str(f))
        assert 'x=1&amp;y=2' in r['content'], r['content']
        assert '&amp;amp;' in r['content'], r['content']


def test_office_text_not_double_unescaped():
    """A cell literally showing &lt;tag&gt; must not become <tag>."""
    main = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    with fake_root() as root:
        f = root / 'esc.xlsx'
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr(
                'xl/worksheets/sheet1.xml',
                f'<worksheet xmlns="{main}"><sheetData><row>'
                '<c t="inlineStr"><is><t>&amp;lt;tag&amp;gt;</t></is></c>'
                '</row></sheetData></worksheet>',
            )
        f.write_bytes(buf.getvalue())
        content = s.sp_read(str(f))['content']
        assert '&lt;tag&gt;' in content, content
        assert '<tag>' not in content, content


def test_size_gate_blocks_huge_files():
    assert s.MAX_READ_BYTES > 0
    with fake_root() as root:
        f = root / 'big.txt'
        f.write_text('x' * 100)
        orig = s.MAX_READ_BYTES
        try:
            s.MAX_READ_BYTES = 10
            assert 'over the' in s.sp_read(str(f)).get('error', '')
            s.MAX_READ_BYTES = orig
            assert 'content' in s.sp_read(str(f))
        finally:
            s.MAX_READ_BYTES = orig


def test_sp_find_bad_subdir_returns_error():
    with fake_root():
        r = s.sp_find('*.xlsx', subdir='/etc')
        assert 'error' in r, r


def test_sp_find_returns_newest_when_truncated():
    """Limit must yield the newest matches, not a walk-order slice."""
    with fake_root() as root:
        for i in range(6):
            f = root / f'f{i}.txt'
            f.write_text('x')
            os.utime(f, (1_700_000_000 + i * 86400,) * 2)
        r = s.sp_find('*.txt', limit=2)
        assert r['truncated'] is True and r['total_matched'] == 6, r
        assert [h['name'] for h in r['results']] == ['f5.txt', 'f4.txt'], r['results']


def test_hydrated_treats_empty_file_as_local():
    class EmptyReal:
        st_size = 0
        st_blocks = 0

    class Placeholder:
        st_size = 21355
        st_blocks = 0

    assert s._is_hydrated(EmptyReal()) is True
    assert s._is_hydrated(Placeholder()) is False


def test_xml_text_survives_bad_xml():
    assert s._xml_text(b'<not xml') == ''
    assert 'hello' in s._xml_text(b'<a><b>hello</b></a>')


def test_read_only_by_default():
    """Writes are opt-in: nothing write-ish is advertised unless SHAREPOINT_ENABLE_WRITES is set."""
    assert s.ENABLE_WRITES is False
    assert s.REGISTERED_WRITE_TOOLS == []
    # The write functions exist so they stay testable, but must not be registered.
    for name in ('sp_list_item_create', 'sp_list_item_update', 'sp_list_item_delete'):
        assert callable(getattr(s, name)), name
    forbidden = {'mail_send', 'mail_reply', 'event_create', 'sp_write', 'mail_move'}
    names = {a for a in dir(s) if callable(getattr(s, a)) and not a.startswith('_')}
    assert not (names & forbidden), names & forbidden


# --- SharePoint list tools (Microsoft Graph) ------------------------------


def test_site_path_forms():
    """URLs, host:path, and composite ids all resolve to a Graph path segment."""
    assert s._site_path('https://steelcase.sharepoint.com/sites/Foo') == 'steelcase.sharepoint.com:/sites/Foo'
    assert s._site_path('https://steelcase.sharepoint.com/sites/Foo/') == 'steelcase.sharepoint.com:/sites/Foo'
    assert s._site_path('steelcase.sharepoint.com:/sites/Foo') == 'steelcase.sharepoint.com:/sites/Foo'
    composite = 'steelcase.sharepoint.com,0dcf,abc'
    assert s._site_path(composite) == composite


def test_resolve_site_id_passthrough():
    """A composite id must not trigger a Graph round-trip."""
    composite = 'steelcase.sharepoint.com,0dcf,abc'
    assert s._resolve_site_id(composite) == composite


def test_list_items_paging_and_limit():
    """Paging is followed and `limit` caps the result with a truncation flag."""
    pages = [
        {
            'value': [{'id': '1', 'fields': {'t': 'a'}}, {'id': '2', 'fields': {'t': 'b'}}],
            '@odata.nextLink': 'https://graph.microsoft.com/next',
        },
        {'value': [{'id': '3', 'fields': {'t': 'c'}}]},
    ]
    calls = []
    orig = s._graph_get
    s._graph_get = lambda url, headers=None: (calls.append(url), pages.pop(0))[1]
    try:
        r = s.sp_list_items('steelcase.sharepoint.com,aaa,bbb', 'L', limit=2)
    finally:
        s._graph_get = orig
    assert r['count'] == 2, r
    assert r['truncated'] is True, r
    assert [i['id'] for i in r['items']] == ['1', '2'], r
    assert len(calls) == 2, calls  # a second page is fetched to detect the overflow


def test_list_items_filter_prefix_and_header():
    """filter_query gains the fields/ prefix; the Prefer header is always sent."""
    seen = {}

    def fake(url, headers=None):
        seen['url'] = url
        seen['headers'] = headers
        return {'value': []}

    orig = s._graph_get
    s._graph_get = fake
    try:
        s.sp_list_items(
            'steelcase.sharepoint.com,aaa,bbb', 'Project Portfolio', filter_query="project_status eq 'In Progress'"
        )
    finally:
        s._graph_get = orig
    assert 'Project%20Portfolio' in seen['url'], seen['url']
    assert "$filter=fields/project_status%20eq%20'In%20Progress'" in seen['url'], seen['url']
    assert seen['headers'] == {'Prefer': 'HonorNonIndexedQueriesWarningMayFailRandomly'}, seen['headers']


def test_graph_error_surfaces_as_dict():
    """A Graph failure must become {'error': ...}, never a raised exception."""

    def boom(url, headers=None):
        raise s.GraphError('boom')

    orig = s._graph_get
    s._graph_get = boom
    try:
        assert s.sp_lists('site') == {'error': 'boom'}
        assert s.sp_list_items('site', 'L') == {'error': 'boom'}
    finally:
        s._graph_get = orig


def test_az_bin_missing_raises_graph_error():
    """An absent `az` is reported as a GraphError, not a bare OSError."""
    orig = s.shutil.which
    s.shutil.which = lambda _name: None
    try:
        try:
            s._az_bin()
        except s.GraphError:
            return
        raise AssertionError('expected GraphError when az is absent')
    finally:
        s.shutil.which = orig


# --- SharePoint list writes (opt-in) --------------------------------------


def test_list_item_create_posts_fields():
    seen = {}

    def fake(url, method='GET', body=None, headers=None, timeout=60):
        seen.update(url=url, method=method, body=body)
        return {'id': '99', 'webUrl': 'https://x/99', 'fields': {'Title': 'T'}}

    orig = s._graph_request
    s._graph_request = fake
    try:
        r = s.sp_list_item_create('steelcase.sharepoint.com,a,b', 'L', {'Title': 'T'})
    finally:
        s._graph_request = orig
    assert seen['method'] == 'POST', seen
    assert seen['body'] == {'fields': {'Title': 'T'}}, seen
    assert r == {'id': '99', 'web_url': 'https://x/99'}, r


def test_list_item_update_patches_only_given_fields():
    seen = {}

    def fake(url, method='GET', body=None, headers=None, timeout=60):
        seen.update(url=url, method=method, body=body)
        return {'Title': 'new'}

    orig = s._graph_request
    s._graph_request = fake
    try:
        r = s.sp_list_item_update('steelcase.sharepoint.com,a,b', 'Project Portfolio', '3', {'Title': 'new'})
    finally:
        s._graph_request = orig
    assert seen['method'] == 'PATCH', seen
    assert seen['body'] == {'Title': 'new'}, seen
    assert '/items/3/fields' in seen['url'], seen
    assert 'Project%20Portfolio' in seen['url'], seen
    assert r == {'id': '3', 'updated': ['Title']}, r


def test_list_item_delete_uses_delete():
    seen = {}

    def fake(url, method='GET', body=None, headers=None, timeout=60):
        seen.update(url=url, method=method)
        return {}

    orig = s._graph_request
    s._graph_request = fake
    try:
        r = s.sp_list_item_delete('steelcase.sharepoint.com,a,b', 'L', '3')
    finally:
        s._graph_request = orig
    assert seen['method'] == 'DELETE', seen
    assert seen['url'].endswith('/items/3'), seen
    assert r == {'deleted': True, 'item_id': '3'}, r


def test_writes_reject_empty_fields():
    assert 'error' in s.sp_list_item_create('steelcase.sharepoint.com,a,b', 'L', {})
    assert 'error' in s.sp_list_item_update('steelcase.sharepoint.com,a,b', 'L', '3', {})


def test_write_error_surfaces_as_dict():
    def boom(url, method='GET', body=None, headers=None, timeout=60):
        raise s.GraphError('nope')

    orig = s._graph_request
    s._graph_request = boom
    try:
        assert s.sp_list_item_create('steelcase.sharepoint.com,a,b', 'L', {'T': 1}) == {'error': 'nope'}
        assert s.sp_list_item_update('steelcase.sharepoint.com,a,b', 'L', '3', {'T': 1}) == {'error': 'nope'}
        assert s.sp_list_item_delete('steelcase.sharepoint.com,a,b', 'L', '3') == {'error': 'nope'}
    finally:
        s._graph_request = orig


if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    failed = 0
    for t in tests:
        try:
            t()
            print(f'ok   {t.__name__}')
        except Exception as e:
            failed += 1
            print(f'FAIL {t.__name__}: {type(e).__name__}: {e}')
    print(f'\n{len(tests) - failed}/{len(tests)} passed')
    raise SystemExit(1 if failed else 0)
