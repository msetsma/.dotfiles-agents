import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

import {
  ACCENT,
  ADDED,
  BAD,
  DEFAULT_SETTINGS,
  IDE,
  LINK,
  MERGED,
  MUTED,
  OK,
  PLAN,
  REMOVED,
  SHELL,
  TEXT,
  TRACK,
  WARN,
  bar,
  duration,
  tokenColor,
  toolColor,
  burnRate,
  compact,
  pillGlyphs,
  describeCall,
  fit,
  fitStart,
  estimateTokens,
  limitLabel,
  loadSettings,
  mcpServers,
  parseGitStatus,
  parseNumstat,
  pushActivity,
  resetLabel,
} from '../hooks/register'
import type { Activity } from '../types'

const SURFACES = ['terminal', 'desktop'] as const

const paneProps = (bodyColumns: number) => ({
  title: 'Sidebar',
  isFocused: false,
  bodyColumns,
  placement: 'dock' as const,
  scroll: { offset: 0, bodyRows: 60 },
  view: {},
})

const PANE = { plugin: 'sidebar', component: 'Pane', requestId: 'sidebar', props: paneProps(44) } as const

// The plugin's `$.store`, held in `saved` so the test can read back what it wrote.
function memoryStore(on: On) {
  const saved: Record<string, unknown> = {}
  on('store.set', ($, e) => {
    saved[e.key] = e.value
    return { value: undefined }
  })
  on('store.get', ($, e) => ({ value: saved[e.key] }))
  return saved
}

// What `/sidebar`'s refresh reads, with these MCP tools connected.
function session(on: On, tools: string[]) {
  on('tool.list', () => ({ value: tools.map(name => ({ name, description: '', mcp: true })) }))
  on('session.usage', () => ({ value: { startedAt: 0, context: { window: 1 }, rateLimits: [] } }))
  on('session.model', () => ({ value: 'opus' }))
  on('session.turns', () => ({ value: 0 }))
  on('agent.list', () => ({ value: [] }))
  on('ui.panes', () => ({ value: [] }))
  on('ui.open', () => ({ value: { isPlaced: true as const } }))
  // git: a clean `main` one ahead of origin, and a numstat for the file the tests edit.
  on('process.run', ($, e) => {
    const stdout =
      e.argv[1] === 'status' ? '# branch.head main\n# branch.ab +1 -0\n1 .M N... 100644 100644 100644 a b src/a.ts\n' : '3\t1\tsrc/a.ts\n'
    return { value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
}

const openSidebar = ($: Engine) =>
  $.command.run({
    command: 'sidebar',
    args: '',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: false, columns: 120 },
  })

const TODOS = [
  { content: 'Write tests', status: 'in_progress' as const, activeForm: 'Writing tests' },
  { content: 'Ship it', status: 'pending' as const, activeForm: 'Shipping' },
]

test('groups MCP tools by server', async () => {
  expect(mcpServers(['mcp__github__get_me', 'mcp__github__list_issues', 'mcp__claude_ai_Claude_Docs__batch'])).toEqual([
    { name: 'claude_ai_Claude_Docs', tools: 1 },
    { name: 'github', tools: 2 },
  ])
})

test('bar clamps and fills', async () => {
  expect(bar(50, 10)).toBe('━━━━━─────')
  expect(bar(150, 4)).toBe('━━━━')
})

test('estimates tokens from args and result text', async () => {
  expect(estimateTokens({}, undefined)).toBe(1)
  expect(estimateTokens({ command: 'ls' }, 'a'.repeat(100))).toBe(Math.ceil((16 + 100) / 4))
})

test('extracts a short target per tool', async () => {
  expect(describeCall('Bash', { command: 'git status\ngit log' })).toEqual({
    tool: 'Bash',
    target: 'git status',
    full: 'git status\ngit log',
  })
  expect(describeCall('Edit', { file_path: '/a/b/c.ts' }).target).toBe('c.ts')
  expect(describeCall('NotebookEdit', { notebook_path: '/n/x.ipynb' }).target).toBe('x.ipynb')
  expect(describeCall('Grep', { pattern: 'TODO' }).target).toBe('TODO')
  expect(describeCall('WebFetch', { url: 'https://example.com/a?b' })).toMatchObject({
    target: 'example.com',
    full: 'https://example.com/a?b',
  })
  expect(describeCall('Agent', { description: 'find it' }).target).toBe('find it')
  expect(describeCall('Skill', { skill: 'pdf' }).target).toBe('pdf')
  expect(describeCall('mcp__github__get_me', { limit: 3, owner: 'me' })).toEqual({
    tool: 'github/get_me',
    target: 'me',
    full: 'me',
  })
  expect(describeCall('LSP', { x: 'y' }).target).toBe('')
  expect(describeCall('Bash', { command: 'x'.repeat(900) }).full).toHaveLength(500)
})

test('merges consecutive calls of one tool on one thread', async () => {
  const call = (tool: string, agent = false): Activity => ({
    id: tool,
    tool,
    target: tool,
    full: tool,
    agent,
    failed: false,
    ms: 10,
    tokens: 5,
    count: 1,
  })
  let list: Activity[] = []
  for (const c of [call('Read'), call('Read'), call('Read', true), call('Bash'), { ...call('Bash'), failed: true }])
    list = pushActivity(list, c)
  expect(list.map(a => [a.tool, a.agent, a.count, a.tokens, a.ms, a.failed])).toEqual([
    ['Read', false, 2, 10, 20, false],
    ['Read', true, 1, 5, 10, false],
    ['Bash', false, 2, 10, 20, true],
  ])
  for (let i = 0; i < 30; i++) list = pushActivity(list, call(`T${i}`))
  expect(list).toHaveLength(20)
})

test('loads settings over the defaults, ignoring garbage', async () => {
  expect(loadSettings(undefined)).toEqual(DEFAULT_SETTINGS)
  expect(loadSettings('nope')).toEqual(DEFAULT_SETTINGS)
  expect(
    loadSettings({ sections: { todos: 'hide', mcp: 'loud', bogus: 'show' }, collapsed: ['git', 'x'], recentCount: 7 }),
  ).toEqual({ ...DEFAULT_SETTINGS, sections: { ...DEFAULT_SETTINGS.sections, todos: 'hide' }, collapsed: ['git'] })
  expect(loadSettings({ prompts: [] }).prompts).toEqual([])
  expect(loadSettings({ prompts: [{ label: 'x' }, { label: 'a', text: 'b' }] }).prompts).toEqual([{ label: 'a', text: 'b' }])
  expect(loadSettings({ prompts: 'x' }).prompts).toEqual(DEFAULT_SETTINGS.prompts)
})

test('pane shows todos written by TodoWrite', async ($, on) => {
  mock.clock(on)
  on('tool.call', { tool: 'TodoWrite' }, () => ({ result: { oldTodos: [], newTodos: TODOS } }))
  await $.tool.call({ tool: 'TodoWrite', todos: TODOS })

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    const text = (await ui.findAll({ type: 'Text' })).map(t => t.text).join('\n')
    expect(text).toContain('Write tests')
    expect(text).toContain('Ship it')
    expect(text).toContain('Todos 0/2')
    await ui.unmount()
  }
})

test('pressing a header collapses its section', async ($, on) => {
  mock.clock(on)
  const saved = memoryStore(on)
  on('tool.call', { tool: 'TodoWrite' }, () => ({ result: { oldTodos: [], newTodos: TODOS } }))
  await $.tool.call({ tool: 'TodoWrite', todos: TODOS })

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'hdr:todos' })
    expect((await ui.find({ key: 'hdr:todos' }))?.text).toContain('▸ Todos 0/2')
    expect(await ui.find({ type: 'Text', text: 'Write tests' })).toBeUndefined()
    expect(saved.settings).toMatchObject({ collapsed: ['todos'] })
    await ui.press({ key: 'hdr:todos' })
    expect(await ui.find({ type: 'Text', text: 'Write tests' })).toBeDefined()
    await ui.unmount()
  }
})

test('settings cycle and persist to the store', async ($, on) => {
  mock.clock(on)
  const saved = memoryStore(on)
  const expected = ['hide', 'show']
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'gear' })
    await ui.press({ key: 'set:todos' })
    expect((await ui.find({ key: 'set:todos' }))?.text).toContain(expected.shift())
    await ui.press({ key: 'done' })
    expect(await ui.find({ key: 'set:todos' })).toBeUndefined()
    await ui.unmount()
  }
  expect(saved.settings).toMatchObject({ sections: { todos: 'show' } })

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'gear' })
  await ui.press({ key: 'set:recent' })
  expect(saved.settings).toMatchObject({ recentCount: 12 })
  await ui.unmount()
})

test('auto hides empty sections, show keeps the placeholder', async ($, on) => {
  mock.clock(on)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect(await ui.find({ key: 'hdr:todos' })).toBeUndefined()
    expect(await ui.find({ key: 'hdr:errors' })).toBeUndefined()
    expect(await ui.find({ key: 'hdr:mcp' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'None connected' })).toBeDefined()
    await ui.unmount()
  }
})

test('MCP usage accumulates per server', async ($, on) => {
  mock.clock(on)
  session(on, ['mcp__github__get_me', 'mcp__claude_ai_Claude_Docs__batch', 'mcp__slack__post'])
  on('tool.call', () => ({ result: {}, text: 'x'.repeat(38) }))
  await $.tool.call({ tool: 'mcp__github__get_me' })
  await $.tool.call({ tool: 'mcp__github__get_me' })
  await $.tool.call({ tool: 'mcp__claude_ai_Claude_Docs__batch', batch: [] })
  await $.tool.call({ tool: 'Edit', file_path: '/repo/src/a.ts', old_string: 'a', new_string: 'b' })

  await openSidebar($)

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    const rows = (await ui.findAll({ type: 'Text', text: /^● / })).map(t => t.text)
    // 13 = estimateTokens({ batch: [] }, 38 chars); 10 per get_me.
    expect(rows).toEqual([
      '● github 1 tools · 2 calls ~20 tok',
      '● claude_ai_Claude_Docs 1 tools · 1 calls ~13 tok',
      '● slack 1 tools',
    ])
    const acts = (await ui.findAll({ type: 'Text', text: /\/(batch|get_me)/ })).map(t => t.text)
    expect(acts).toEqual(['claude_ai_Claude_Docs/batch', 'github/get_me ×2'])
    expect((await ui.find({ key: 'git:src/a.ts' }))?.text).toMatch(/^src\/a\.ts ·+ \+3 −1$/)
    expect((await ui.find({ key: 'git:branch' }))?.text).toBe('main ↑1 ↓0 · 1 changed')
    expect((await ui.find({ key: 'hdr:mcp' }))?.text).toContain('~33 tok')
    await ui.unmount()
  }
})

const THEME_KEYS = [
  'text', 'inverseText', 'inactive', 'subtle', 'suggestion', 'remember', 'success', 'error', 'warning', 'merged',
  'claude', 'permission', 'planMode', 'autoAccept', 'promptBorder', 'bashBorder', 'ide', 'diffAdded', 'diffRemoved',
  'diffAddedDimmed', 'diffRemovedDimmed', 'diffAddedWord', 'diffRemovedWord',
]

test('colors are theme keys, not fixed colors', async () => {
  const colors = [OK, WARN, BAD, ACCENT, TRACK, TEXT, MUTED, LINK, PLAN, MERGED, IDE, SHELL, ADDED, REMOVED]
  for (const color of colors) expect(THEME_KEYS).toContain(color)
})

test('tool and token colors by kind and size', async () => {
  expect(toolColor('Bash')).toBe(SHELL)
  for (const t of ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch']) expect(toolColor(t)).toBe(LINK)
  for (const t of ['Edit', 'Write', 'MultiEdit', 'NotebookEdit']) expect(toolColor(t)).toBe(ADDED)
  expect(toolColor('github/get_me')).toBe(IDE)
  for (const t of ['Agent', 'Task', 'Skill', 'SubagentHandback', 'LSP']) expect(toolColor(t)).toBe(PLAN)
  expect([999, 1000, 9999, 10000].map(tokenColor)).toEqual([TRACK, WARN, WARN, BAD])
})

test('session length reads as hours and minutes from an hour on', async () => {
  expect(duration(45)).toBe('45m')
  expect(duration(60)).toBe('1h0m')
  expect(duration(160)).toBe('2h40m')
})

test('header rows fill the dock without wrapping, open or collapsed', async ($, on) => {
  mock.clock(on)
  memoryStore(on)
  session(on, ['mcp__github__get_me'])
  on('tool.call', () => ({ result: {}, text: 'ok' }))
  await $.tool.call({ tool: 'Edit', file_path: '/repo/src/a-very-long-file-name-that-goes-on-and-on.ts', old_string: 'a', new_string: 'b' })
  await openSidebar($)
  const ids = ['usage', 'git', 'prompts', 'mcp', 'activity']
  for (const columns of [44, 30]) {
    const ui = await $.ui.mount({ ...PANE, surface: 'terminal', props: paneProps(columns) })
    for (const pass of ['open', 'collapsed']) {
      for (const id of ids) {
        const text = (await ui.find({ key: `hdr:${id}` }))?.text ?? ''
        expect(text).toMatch(pass === 'open' ? /^▾ .*─$/ : /^▸ .*─/)
        expect(text.length).toBe(columns - 2)
      }
      for (const id of ids) await ui.press({ key: `hdr:${id}` })
    }
    await ui.unmount()
  }
})

test('usage meters fit a narrow dock', async ($, on) => {
  mock.clock(on)
  on('session.measure', ($, e) => ({ changed: e.changed }))
  expect(limitLabel('five_hour')).toBe('5h')
  expect(limitLabel('seven_day')).toBe('7d')
  await $.session.measure({
    context: { tokens: 176000, window: 1000000, percent: 18 },
    rateLimits: [
      { kind: 'five_hour', percentUsed: 19 },
      { kind: 'seven_day', percentUsed: 9 },
    ],
    cost: { usd: 3.47 },
    changed: ['context', 'rateLimits', 'cost'],
  })
  const columns = 30
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface, props: paneProps(columns) })
    const lines = (await ui.findAll({ type: 'Text' })).map(t => t.text)
    const meters = lines.filter(l => /^(ctx|5h|7d)\s.*%$/.test(l))
    expect(meters).toHaveLength(3)
    for (const line of meters) expect(line.length).toBeLessThanOrEqual(columns - 2)
    await ui.unmount()
  }
})

test('activity rows fit the pane and prefer the Bash description', async () => {
  expect(fit('abcdef', 4)).toBe('abc…')
  expect(fit('abc', 4)).toBe('abc')
  expect(describeCall('Bash', { command: 'cd /x && git status' }).target).toBe('git status')
  expect(describeCall('Bash', { command: 'cd /x && ls', description: 'List files' }).target).toBe('List files')
})

test('pressing an activity row opens its detail', async ($, on) => {
  mock.clock(on)
  session(on, [])
  on('tool.call', () => ({ result: {}, text: 'ok' }))
  await $.tool.call({ tool: 'Bash', tool_use_id: 'b1', command: 'cd /x && make test', description: 'Run tests' })
  await openSidebar($)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect(await ui.find({ key: 'copy:b1' })).toBeUndefined()
    await ui.press({ key: 'act:b1' })
    expect(await ui.find({ key: 'copy:b1' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'cd /x && make test' })).toBeDefined()
    await ui.press({ key: 'act:b1' })
    expect(await ui.find({ key: 'copy:b1' })).toBeUndefined()
    await ui.unmount()
  }
})

test('compact reads in k and M', async () => {
  expect(compact(136_000)).toBe('136k')
  expect(compact(1_000_000)).toBe('1M')
  expect(compact(1_250_000)).toBe('1.3M')
})

test('nerd pill caps and fills', async () => {
  expect(pillGlyphs(0, 3)).toBe('\uee00\uee01\uee02')
  expect(pillGlyphs(2, 3)).toBe('\uee03\uee04\uee02')
  expect(pillGlyphs(3, 3)).toBe('\uee03\uee04\uee05')
})

test('reset label reads local time, with the weekday when not today', async () => {
  // Built in local time, so the assertions hold in any timezone. 9 Oct 2026 is a Friday.
  const now = new Date(2026, 9, 9, 8, 0).getTime()
  expect(resetLabel(new Date(2026, 9, 9, 14, 20).toISOString(), now)).toBe('14:20')
  expect(resetLabel(new Date(2026, 9, 10, 9, 5).toISOString(), now)).toBe('Sat 09:05')
  expect(resetLabel('garbage', now)).toBe('')
})

test('burn rate per hour and per turn', async () => {
  const start = 1_000_000
  expect(burnRate(3, start, 4, start + 2 * 3_600_000)).toBe('$1.50/hr · $0.75/turn')
  expect(burnRate(undefined, start, 4, start + 1)).toBe('')
  expect(burnRate(3, start, 0, start + 1)).toBe('')
})

test('parses git porcelain v2 status', async () => {
  const out = [
    '# branch.oid abc',
    '# branch.head main',
    '# branch.upstream origin/main',
    '# branch.ab +2 -3',
    '1 .M N... 100644 100644 100644 a b x.ts',
    '1 A. N... 000000 100644 100644 a b dir/with space.ts',
    '2 R. N... 100644 100644 100644 a b R100 new name.ts\told.ts',
    'u UU N... 100644 100644 100644 100644 a b c conflict.ts',
    '? new.ts',
    '',
  ].join('\n')
  expect(parseGitStatus(out)).toEqual({
    branch: 'main',
    ahead: 2,
    behind: 3,
    files: [
      { path: 'x.ts', status: 'M' },
      { path: 'dir/with space.ts', status: 'A' },
      { path: 'new name.ts', status: 'R' },
      { path: 'conflict.ts', status: 'U' },
      { path: 'new.ts', status: '?' },
    ],
  })
  expect(parseGitStatus('# branch.head feat\n')).toEqual({ branch: 'feat', files: [] })
})

test('parses numstat, skipping binaries', async () => {
  expect(parseNumstat('12\t3\tsrc/a.ts\n-\t-\timg.png\n0\t5\tb.md\n')).toEqual({
    'src/a.ts': { add: 12, del: 3 },
    'b.md': { add: 0, del: 5 },
  })
})

test('a quick prompt fills the prompt box, and the editor adds and removes one', async ($, on) => {
  mock.clock(on)
  const saved = memoryStore(on)
  const filled: string[] = []
  on('prompt.fill', ($, e) => {
    filled.push(e.text)
    return { isFilled: true }
  })
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'qp:0' })
    await ui.press({ key: 'qp:edit' })
    await ui.press({ key: 'qp:new' })
    await ui.input({ key: 'qp:name', text: `deploy ${surface}`, kind: 'change' })
    await ui.input({ key: 'qp:text', text: 'ship it', kind: 'change' })
    await ui.press({ key: 'qp:save' })
    await ui.press({ key: 'qp:del:0' })
    await ui.press({ key: 'qp:done' })
    await ui.unmount()
  }
  expect(filled).toEqual(['commit the changes', 'run the tests'])
  expect(saved.settings).toMatchObject({
    prompts: [
      { label: 'review', text: 'review the diff for bugs' },
      { label: 'deploy terminal', text: 'ship it' },
      { label: 'deploy desktop', text: 'ship it' },
    ],
  })
})

test('a failed call lands in Errors and expands on press', async ($, on) => {
  mock.clock(on)
  on('tool.call', () => ({ result: {}, text: 'exit 2: no such file', isError: true }))
  await $.tool.call({ tool: 'Bash', tool_use_id: 'e1', command: 'cat nope', description: 'Read nope' })
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect((await ui.find({ key: 'err:e1' }))?.text).toMatch(/^✗ Bash Read nope/)
    expect(await ui.find({ key: 'errcopy:e1' })).toBeUndefined()
    await ui.press({ key: 'err:e1' })
    expect(await ui.find({ type: 'Text', text: 'exit 2: no such file' })).toBeDefined()
    expect(await ui.find({ key: 'errcopy:e1' })).toBeDefined()
    await ui.press({ key: 'err:e1' })
    expect(await ui.find({ key: 'errcopy:e1' })).toBeUndefined()
    await ui.unmount()
  }
})

test('fitStart keeps the end of a path', async () => {
  expect(fitStart('a/b/c/file.ts', 8)).toBe('…file.ts')
  expect(fitStart('file.ts', 8)).toBe('file.ts')
})

test('editing a quick prompt replaces it; cancel and a missing name change nothing', async ($, on) => {
  mock.clock(on)
  const saved = memoryStore(on)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'qp:edit' })
  await ui.press({ key: 'qp:change:0' })
  await ui.input({ key: 'qp:name', text: 'ship', kind: 'change' })
  await ui.input({ key: 'qp:text', text: 'commit and push' })
  await ui.press({ key: 'qp:new' })
  await ui.input({ key: 'qp:text', text: 'no name', kind: 'change' })
  await ui.press({ key: 'qp:save' })
  await ui.press({ key: 'qp:cancel' })
  await ui.unmount()
  expect((saved.settings as { prompts: unknown[] }).prompts).toEqual([
    { label: 'ship', text: 'commit and push' },
    { label: 'tests', text: 'run the tests' },
    { label: 'review', text: 'review the diff for bugs' },
  ])
})
