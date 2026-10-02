"""Probe the internal Teams endpoints our parsers depend on.

These APIs are undocumented and can drift. When something breaks, run this to
see the current shape, then update ``tests/fixtures/`` (with scrubbed values) and
the matching contract test.

    uv run python scripts/recon.py                # probe everything
    uv run python scripts/recon.py chats files    # probe selected targets
    uv run python scripts/recon.py --dump /tmp/x  # write raw payloads

Secrets are never printed; only status codes, counts and key names.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from teams_browser.api.http import HttpClient  # noqa: E402
from teams_browser.auth.session import Paths, load_session  # noqa: E402
from teams_browser.auth.tokens import extract_region_config, extract_tokens  # noqa: E402
from teams_browser.config import substrate_base_url  # noqa: E402

TARGETS = ('calendar', 'chats', 'messages', 'calllogs', 'files', 'transcript')


def _print_keys(label: str, obj: Any, depth: int = 2, _level: int = 0) -> None:
    pad = '  ' * _level
    if _level > depth:
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            print(f'{pad}{key}: {type(value).__name__}')
            _print_keys(label, value, depth, _level + 1)
    elif isinstance(obj, list) and obj:
        print(f'{pad}[0] of {len(obj)}')
        _print_keys(label, obj[0], depth, _level + 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('targets', nargs='*', default=list(TARGETS))
    parser.add_argument('--dump', type=Path, help='Directory for raw JSON payloads.')
    args = parser.parse_args()

    unknown = set(args.targets) - set(TARGETS)
    if unknown:
        parser.error(f'unknown target(s): {", ".join(sorted(unknown))}')

    state = load_session(Paths.default())
    if state is None:
        print('No session. Run `teams-browser login` first.', file=sys.stderr)
        return 2

    tokens = extract_tokens(state)
    region = extract_region_config(state)
    if region is None:
        print('Could not determine region from the session.', file=sys.stderr)
        return 2

    print(f'region={region.region_partition}  base={region.teams_base_url}')
    print(f'tokens: skype={bool(tokens.skype_token)} substrate={bool(tokens.substrate)}')

    http = HttpClient()
    skype = {'Authentication': f'skypetoken={tokens.skype_token}', 'Accept': 'application/json'}
    substrate = {
        'Authorization': f'Bearer {tokens.substrate.token}' if tokens.substrate else '',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'Prefer': 'substrate.flexibleschema,outlook.data-source="Substrate",exchange.behavior="SubstrateFiles"',
    }

    if args.dump:
        args.dump.mkdir(parents=True, exist_ok=True)

    def dump(name: str, payload: Any) -> None:
        if args.dump:
            (args.dump / f'{name}.json').write_text(json.dumps(payload, indent=2, default=str))
            print(f'    wrote {args.dump / f"{name}.json"}')

    try:
        if 'calendar' in args.targets:
            print('\n== calendarView ==')
            response = http.request(
                'GET',
                f'{region.teams_base_url}/api/mt/'
                f'{"part/" if region.has_partition else ""}{region.region_partition}'
                '/v2.1/me/calendars/calendarView',
                headers=skype,
                params={'$top': '5', '$select': 'subject,startTime,skypeTeamsData,isOnlineMeeting'},
            )
            print(f'  status={response.status_code} bytes={len(response.content)}')
            if response.status_code < 400:
                values = response.json().get('value') or []
                print(f'  meetings={len(values)}')
                if values:
                    _print_keys('calendar', values[0])
                dump('calendar_view', response.json())

        if 'chats' in args.targets:
            print('\n== chatsvc conversations ==')
            response = http.request(
                'GET',
                f'{region.chat_service_url}/v1/users/ME/conversations',
                headers=skype,
                params={'$top': '20', 'view': 'msnp24Equivalent', 'pageSize': '20'},
            )
            print(f'  status={response.status_code} bytes={len(response.content)}')
            if response.status_code < 400:
                conversations = response.json().get('conversations') or []
                kinds: dict[str, int] = {}
                for conversation in conversations:
                    cid = conversation.get('id') or ''
                    kind = (
                        'channel'
                        if '@thread.tacv2' in cid
                        else 'meeting'
                        if cid.startswith('19:meeting_')
                        else 'one_to_one'
                        if '@unq.gbl.spaces' in cid
                        else 'group'
                    )
                    kinds[kind] = kinds.get(kind, 0) + 1
                print(f'  conversations={len(conversations)} kinds={kinds}')
                if conversations:
                    _print_keys('chats', conversations[0])
                dump('chatsvc_conversations', response.json())

        if 'messages' in args.targets:
            print('\n== chatsvc messages ==')
            response = http.request(
                'GET',
                f'{region.chat_service_url}/v1/users/ME/conversations',
                headers=skype,
                params={'$top': '1', 'view': 'msnp24Equivalent'},
            )
            first = (response.json().get('conversations') or [{}])[0] if response.status_code < 400 else {}
            if first.get('id'):
                response = http.request(
                    'GET',
                    f'{region.chat_service_url}/v1/users/ME/conversations/{first["id"]}/messages',
                    headers=skype,
                    params={'pageSize': '5', 'view': 'msnp24Equivalent'},
                )
                print(f'  status={response.status_code} bytes={len(response.content)}')
                if response.status_code < 400:
                    messages = response.json().get('messages') or []
                    print(f'  messages={len(messages)} types={sorted({m.get("messagetype") for m in messages})}')
                    if messages:
                        _print_keys('messages', messages[0], depth=3)
                    dump('chatsvc_messages', response.json())
            else:
                print('  no conversation available')

        if 'calllogs' in args.targets:
            print('\n== chatsvc 48:calllogs ==')
            response = http.request(
                'GET',
                f'{region.chat_service_url}/v1/users/ME/conversations/48:calllogs/messages',
                headers=skype,
                params={'pageSize': '50', 'view': 'msnp24Equivalent'},
            )
            print(f'  status={response.status_code} bytes={len(response.content)}')
            if response.status_code < 400:
                messages = response.json().get('messages') or []
                types: dict[str, int] = {}
                for message in messages:
                    kind = str(message.get('messagetype'))
                    types[kind] = types.get(kind, 0) + 1
                print(f'  messages={len(messages)} types={types}')
                with_thread = [
                    m
                    for m in messages
                    if m.get('messagetype') in ('RichText/Media_CallLogTranscript', 'RichText/Media_CallLogRecording')
                ]
                print(f'  calls with a thread id={len(with_thread)}')
                if with_thread:
                    _print_keys('calllogs', with_thread[0], depth=1)
                dump('calllogs_messages', response.json())

        if 'files' in args.targets:
            print('\n== WorkingSetFiles ==')
            response = http.request(
                'GET',
                f'{substrate_base_url()}/api/beta/me/WorkingSetFiles/',
                headers=substrate,
                params={
                    '$top': '50',
                    '$orderby': 'FileCreatedTime desc',
                    '$select': 'Visualization,FileName,FileExtension,FileCreatedTime,'
                    'ItemProperties/Default/MeetingThreadId',
                },
            )
            print(f'  status={response.status_code} bytes={len(response.content)}')
            if response.status_code < 400:
                values = response.json().get('value') or []
                kinds: dict[str, int] = {}
                for item in values:
                    kind = str((item.get('Visualization') or {}).get('Type'))
                    kinds[kind] = kinds.get(kind, 0) + 1
                print(f'  files={len(values)} types={kinds}')
                if values:
                    _print_keys('files', values[0])
                dump('working_set_files', response.json())

        if 'transcript' in args.targets:
            print('\n== Substrate transcript carrier ==')
            response = http.request(
                'GET',
                f'{substrate_base_url()}/api/beta/me/WorkingSetFiles/',
                headers=substrate,
                params={
                    '$top': '50',
                    '$orderby': 'FileCreatedTime desc',
                    '$select': 'Visualization,ItemProperties/Default/MeetingThreadId,'
                    'ItemProperties/Default/TranscriptJson,'
                    'ItemProperties/Default/RecordingStartDateTime',
                },
            )
            print(f'  status={response.status_code} bytes={len(response.content)}')
            if response.status_code < 400:
                values = response.json().get('value') or []
                with_transcript = [
                    v for v in values if (v.get('ItemProperties') or {}).get('Default', {}).get('TranscriptJson')
                ]
                print(f'  items={len(values)} with_transcript={len(with_transcript)}')
                if with_transcript:
                    props = with_transcript[0]['ItemProperties']['Default']
                    parsed = json.loads(props['TranscriptJson'])
                    entries = parsed.get('entries') or []
                    print(f'  entries={len(entries)}')
                    if entries:
                        print(f'  entry keys={sorted(entries[0].keys())}')
                        print(f'  startOffset sample={entries[0].get("startOffset")!r}')
                    dump('transcript_entries', entries[:5])
    finally:
        http.close()

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
