import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderSurface, ThemeKey } from 'claude-code'

import type {
  Activity,
  Agent,
  DiffStat,
  ErrorRow,
  GitInfo,
  Look,
  McpServer,
  McpUsage,
  PromptDraft,
  QuickPrompt,
  Running,
  SkillGroup,
  SectionId,
  Settings,
  Todo,
  Usage,
  Visibility,
} from '../types'

const PANE = 'sidebar'
const REFRESH_MS = 5000
const KEEP_ACTIVITY = 20
const SHOW_GIT_FILES = 10
const KEEP_ERRORS = 10
// Animation: frames redraw this often while something moves; a bar glides to a new value this long.
const FRAME_MS = 100
const GLIDE_MS = 600
// Theme keys, so the pane paints exactly as Claude Code's own UI does: under an
// ANSI theme they resolve to the terminal's palette slots and follow Ghostty.
// Bare names ('red') become fixed hex and `ansi:` strings are refused.
export const OK: ThemeKey = 'success'
export const WARN: ThemeKey = 'warning'
export const BAD: ThemeKey = 'error'
export const ACCENT: ThemeKey = 'claude'
export const TRACK: ThemeKey = 'subtle'
export const TEXT: ThemeKey = 'text'
export const MUTED: ThemeKey = 'inactive'
export const LINK: ThemeKey = 'suggestion'
export const PLAN: ThemeKey = 'planMode'
export const MERGED: ThemeKey = 'merged'
export const IDE: ThemeKey = 'ide'
export const SHELL: ThemeKey = 'bashBorder'
// Text colors: the theme's diffAdded/diffRemoved keys are line backgrounds and vanish as text.
export const ADDED: ThemeKey = 'success'
export const REMOVED: ThemeKey = 'error'

const TITLES: Record<SectionId, string> = {
  usage: 'Usage',
  git: 'Git',
  prompts: 'Prompts',
  skills: 'Commands',
  todos: 'Todos',
  agents: 'Subagents',
  mcp: 'MCP servers',
  activity: 'Activity',
  errors: 'Errors',
}
const SECTION_IDS = Object.keys(TITLES) as SectionId[]
const VISIBILITY_COLORS: Record<Visibility, ThemeKey> = { show: TEXT, auto: TRACK, hide: MUTED }
const VISIBILITY: Visibility[] = ['show', 'auto', 'hide']
// Changed files from git: color and right-hand label per porcelain status (numstat replaces the label when git has one).
const GIT_COLORS: Record<string, ThemeKey> = { M: WARN, A: ADDED, '?': ADDED, D: REMOVED, R: LINK, C: LINK, U: BAD }
const GIT_LABELS: Record<string, string> = { '?': 'new', A: 'added', D: 'deleted', R: 'renamed', C: 'copied', U: 'conflict' }
const RECENT_COUNTS = [4, 8, 12, 16]
// Each look setting: its name in settings and the values a press cycles through, the default first.
export const LOOK: { [K in keyof Look]: { label: string; values: Look[K][] } } = {
  glyphs: { label: 'Icons', values: ['unicode', 'nerd', 'ascii'] },
  barStyle: { label: 'Bars', values: ['line', 'block', 'shade', 'smooth', 'pill', 'dots', 'braille', 'ascii', 'nerd'] },
  barColor: { label: 'Bar color', values: ['gradient', 'solid', 'accent'] },
  barWidth: { label: 'Bar width', values: ['fit', '10', '16', '24'] },
  headerStyle: { label: 'Headers', values: ['rule', 'dots', 'heavy', 'plain'] },
  density: { label: 'Spacing', values: ['cozy', 'compact'] },
  spinner: { label: 'Spinner', values: ['dots', 'arc', 'circle', 'bounce', 'line', 'nerd', 'off'] },
  barMotion: { label: 'Bar motion', values: ['on', 'off'] },
}
const LOOK_KEYS = Object.keys(LOOK) as (keyof Look)[]
const DEFAULT_LOOK = Object.fromEntries(LOOK_KEYS.map(k => [k, LOOK[k].values[0]])) as Look
// Every mark the pane draws, per icon set. `nerd` needs a Nerd Font; `ascii` suits any font.
export const GLYPHS: Record<Look['glyphs'], Record<'open' | 'closed' | 'done' | 'fail' | 'live' | 'todo' | 'doing' | 'reset' | 'sub' | 'gear' | 'branch' | 'server', string>> = {
  unicode: { open: '▾', closed: '▸', done: '✓', fail: '✗', live: '●', todo: '○', doing: '▶', reset: '↻', sub: '↳', gear: '⚙', branch: '', server: '●' },
  nerd: { open: '\uf078', closed: '\uf054', done: '\uf00c', fail: '\uf00d', live: '\uf111', todo: '\uf10c', doing: '\uf04b', reset: '\uf021', sub: '↳', gear: '\uf013', branch: '\ue725 ', server: '\uf1e6' },
  ascii: { open: 'v', closed: '>', done: '+', fail: 'x', live: '*', todo: 'o', doing: '>', reset: '~', sub: '>', gear: '*', branch: '', server: '*' },
}
export const SPINNERS: Record<Exclude<Look['spinner'], 'off'>, string> = {
  dots: '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏',
  arc: '◜◠◝◞◡◟',
  circle: '◐◓◑◒',
  bounce: '⠁⠂⠄⡀⢀⠠⠐⠈',
  line: '|/-\\',
  // Fira Code's spinner glyphs, which Nerd Fonts carry beside the progress bar ones.
  nerd: '\uee06\uee07\uee08\uee09\uee0a\uee0b',
}
// Filled and empty cell per bar style; `smooth` and `nerd` draw their own.
const BAR_CHARS: Record<Exclude<Look['barStyle'], 'smooth' | 'nerd'>, [string, string]> = {
  line: ['━', '─'],
  block: ['█', '░'],
  shade: ['▓', '░'],
  pill: ['▰', '▱'],
  dots: ['●', '○'],
  braille: ['⣿', '⣀'],
  ascii: ['#', '-'],
}
const HEADER_RULES: Record<Look['headerStyle'], string> = { rule: '─', dots: '·', heavy: '━', plain: ' ' }
const EIGHTHS = ' ▏▎▍▌▋▊▉'
export const DEFAULT_SETTINGS: Settings = {
  sections: {
    usage: 'show',
    git: 'auto',
    prompts: 'show',
    skills: 'auto',
    todos: 'auto',
    agents: 'auto',
    mcp: 'show',
    activity: 'show',
    errors: 'auto',
  },
  collapsed: [],
  recentCount: 8,
  look: DEFAULT_LOOK,
  openSkillGroups: [],
  prompts: [
    { label: 'commit', text: 'commit the changes' },
    { label: 'tests', text: 'run the tests' },
    { label: 'review', text: 'review the diff for bugs' },
  ],
}
const EDIT_TOOLS = ['Edit', 'Write', 'MultiEdit', 'NotebookEdit']
const READ_TOOLS = ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch']
// Keys `tool.call` carries beside the tool's own arguments.
const RESERVED = ['tool', 'tool_use_id', 'agentId', 'consent', 'requestMeta']

const usage = atom({ plugin: 'sidebar', key: 'usage' } as const, {
  model: '',
  turns: 0,
  startedAt: 0,
  contextWindow: 0,
  rateLimits: [],
} as Usage)
const todos = atom({ plugin: 'sidebar', key: 'todos' } as const, [] as Todo[])
const agents = atom({ plugin: 'sidebar', key: 'agents' } as const, [] as Agent[])
const mcp = atom({ plugin: 'sidebar', key: 'mcp' } as const, [] as McpServer[])
const skills = atom({ plugin: 'sidebar', key: 'skills' } as const, [] as SkillGroup[])
const mcpUsage = atom({ plugin: 'sidebar', key: 'mcpUsage' } as const, {} as McpUsage)
const activity = atom({ plugin: 'sidebar', key: 'activity' } as const, [] as Activity[])
const running = atom({ plugin: 'sidebar', key: 'running' } as const, [] as Running[])
const git = atom({ plugin: 'sidebar', key: 'git' } as const, null as GitInfo | null)
const diffStat = atom({ plugin: 'sidebar', key: 'diffStat' } as const, {} as DiffStat)
const errors = atom({ plugin: 'sidebar', key: 'errors' } as const, [] as ErrorRow[])
const toolCalls = atom({ plugin: 'sidebar', key: 'toolCalls' } as const, 0)
const settings = atom({ plugin: 'sidebar', key: 'settings' } as const, DEFAULT_SETTINGS)
const view = atom({ plugin: 'sidebar', key: 'view' } as const, 'main' as 'main' | 'settings')
// The id of the Activity row (or `err:` + id of the Errors row) opened for detail, '' for none.
const expanded = atom({ plugin: 'sidebar', key: 'expanded' } as const, '')
const editingPrompts = atom({ plugin: 'sidebar', key: 'editingPrompts' } as const, false)
const promptDraft = atom({ plugin: 'sidebar', key: 'promptDraft' } as const, null as PromptDraft | null)

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)
const cycle = <T,>(list: readonly T[], value: T) => list[(list.indexOf(value) + 1) % list.length] as T
const str = (v: unknown) => (typeof v === 'string' ? v : '')
const basename = (path: string) => path.slice(path.lastIndexOf('/') + 1)

/** The server of an `mcp__<server>__<tool>` name, else undefined. */
export function mcpServerOf(name: string): string | undefined {
  const cut = name.lastIndexOf('__')
  return name.startsWith('mcp__') && cut > 5 ? name.slice(5, cut) : undefined
}

/** A command as its chip shows it: without a `package:` prefix. */
const chipName = (name: string) => name.slice(name.indexOf(':') + 1)

/**
 * Every non-built-in slash command (skills, plugin and user commands, MCP prompts), grouped by
 * where it comes from: the plugin that adds it, `personal` / `project` for the user's own
 * skill and command folders (`local` maps a name to one of those), `<server> (mcp)` for MCP
 * prompts, and `claude.ai` for any other user-level skill (the account's own, `anthropic-skills:`
 * ones included). Names are what
 * to type after `/`, sorted and without duplicates.
 */
export function skillGroups(
  commands: { name: string; source: string; plugin?: string }[],
  local: Record<string, 'personal' | 'project'> = {},
): SkillGroup[] {
  // Per group, chip name → command: one chip per name (`pdf` and `anthropic-skills:pdf` read the same).
  const groups = new Map<string, Map<string, string>>()
  for (const c of commands) {
    if (c.source === 'builtin') continue
    const name = c.name.replace(/ \(MCP\)$/, '')
    const cut = name.indexOf(':')
    const prefix = cut > 0 ? name.slice(0, cut) : undefined
    const group =
      c.source === 'mcp'
        ? `${prefix ?? 'mcp'} (mcp)`
        : c.source === 'plugin'
          ? (c.plugin ?? prefix ?? 'plugins')
          : (local[name] ?? (prefix === 'anthropic-skills' ? undefined : prefix) ?? 'claude.ai')
    const chips = groups.get(group) ?? new Map<string, string>()
    if (!chips.has(chipName(name))) chips.set(chipName(name), name)
    groups.set(group, chips)
  }
  return [...groups]
    .map(([group, chips]) => ({ group, names: [...chips].sort(([a], [b]) => a.localeCompare(b)).map(([, name]) => name) }))
    .sort((a, b) => a.group.localeCompare(b.group))
}

/** Skill and command names in the user's own folders, `~/.claude/...` personal and `./.claude/...` project. */
async function localCommands($: EngineInterface): Promise<Record<string, 'personal' | 'project'>> {
  const home = await $.env.get('HOME')
  const found: Record<string, 'personal' | 'project'> = {}
  const dirs: [string, 'personal' | 'project'][] = [
    [`${home}/.claude/skills`, 'personal'],
    [`${home}/.claude/commands`, 'personal'],
    ['.claude/skills', 'project'],
    ['.claude/commands', 'project'],
  ]
  for (const [dir, where] of dirs) {
    const entries = await $.fs.list(dir).catch(() => [])
    for (const e of entries) found[e.name.replace(/\.md$/, '')] ??= where
  }
  return found
}

export function mcpServers(toolNames: string[]): McpServer[] {
  const counts = new Map<string, number>()
  for (const name of toolNames) {
    const server = mcpServerOf(name) ?? name
    counts.set(server, (counts.get(server) ?? 0) + 1)
  }
  return [...counts].map(([name, tools]) => ({ name, tools })).sort((a, b) => a.name.localeCompare(b.name))
}

/** Rough token cost of a call: its arguments plus the result the model reads, at ~4 chars a token. */
export function estimateTokens(args: Record<string, unknown>, text?: string): number {
  return Math.ceil((JSON.stringify(args).length + (text?.length ?? 0)) / 4)
}

function host(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

/** The row's tool name, its short target and the full value a press copies. */
export function describeCall(tool: string, args: Record<string, unknown>): Pick<Activity, 'tool' | 'target' | 'full'> {
  const server = mcpServerOf(tool)
  let full = ''
  let shown: string | undefined
  if (tool === 'Bash') {
    full = str(args.command)
    // The model's own description reads better than a `cd ... && ...` chain.
    shown = str(args.description) || full.replace(/^cd \S+ && /, '')
  }
  else if (EDIT_TOOLS.includes(tool) || tool === 'Read') {
    full = str(args.file_path ?? args.notebook_path)
    shown = basename(full)
  } else if (tool === 'Grep' || tool === 'Glob') full = str(args.pattern)
  else if (tool === 'WebFetch') {
    full = str(args.url)
    shown = host(full)
  } else if (tool === 'WebSearch') full = str(args.query)
  else if (tool === 'Agent' || tool === 'Task') full = str(args.description)
  else if (tool === 'Skill') full = str(args.skill)
  else if (server !== undefined) full = str(Object.values(args).find(v => typeof v === 'string'))
  return {
    tool: server === undefined ? tool : `${server}/${tool.slice(tool.lastIndexOf('__') + 2)}`,
    target: (shown ?? full).split('\n')[0] ?? '',
    full: full.slice(0, 500),
  }
}

/** Appends a call, folding it into the last row when the same call ran again on the same thread. */
export function pushActivity(list: Activity[], call: Activity): Activity[] {
  const last = list.at(-1)
  const repeat = last?.tool === call.tool && last.target === call.target && last.agent === call.agent
  if (!repeat) return [...list, call].slice(-KEEP_ACTIVITY)
  const merged = { ...call, id: last.id, count: last.count + call.count, tokens: last.tokens + call.tokens, ms: last.ms + call.ms }
  return [...list.slice(0, -1), merged]
}

/** Stored settings over the defaults; anything unknown or malformed falls back. */
export function loadSettings(raw: unknown): Settings {
  const r = (raw ?? {}) as {
    sections?: Record<string, unknown>
    collapsed?: unknown
    recentCount?: unknown
    look?: Record<string, unknown>
    nerdFont?: unknown
    prompts?: unknown
    openSkillGroups?: unknown
  }
  const sections = { ...DEFAULT_SETTINGS.sections }
  for (const id of SECTION_IDS) {
    const v = r.sections?.[id] as Visibility
    if (VISIBILITY.includes(v)) sections[id] = v
  }
  const collapsed = Array.isArray(r.collapsed) ? r.collapsed.filter(id => SECTION_IDS.includes(id)) : []
  const recentCount = RECENT_COUNTS.includes(r.recentCount as number) ? (r.recentCount as number) : DEFAULT_SETTINGS.recentCount
  // A stored list, even an empty one, is the person's; only a missing or malformed one takes the defaults.
  const prompts = Array.isArray(r.prompts)
    ? r.prompts.filter((p): p is QuickPrompt => typeof p?.label === 'string' && !!p.label && typeof p.text === 'string')
    : DEFAULT_SETTINGS.prompts
  const openSkillGroups = Array.isArray(r.openSkillGroups) ? r.openSkillGroups.filter(g => typeof g === 'string') : []
  // The old on/off Nerd Font setting becomes the Nerd Font icons and bars.
  const look: Look = r.look || r.nerdFont !== true ? { ...DEFAULT_LOOK } : { ...DEFAULT_LOOK, glyphs: 'nerd', barStyle: 'nerd' }
  for (const k of LOOK_KEYS) {
    const v = r.look?.[k]
    if ((LOOK[k].values as unknown[]).includes(v)) (look as Record<string, unknown>)[k] = v
  }
  return { sections, collapsed, recentCount, look, prompts, openSkillGroups }
}

const pad2 = (n: number) => String(n).padStart(2, '0')
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

/** A rate-limit reset as local `HH:MM` when it falls today, else `Ddd HH:MM`; '' when unreadable. */
export function resetLabel(resetsAt: string, now: number): string {
  const at = new Date(resetsAt)
  if (Number.isNaN(at.getTime())) return ''
  const time = `${pad2(at.getHours())}:${pad2(at.getMinutes())}`
  return at.toDateString() === new Date(now).toDateString() ? time : `${DAYS[at.getDay()]} ${time}`
}

/** `$X/hr · $Y/turn`, or '' when the cost is unknown or no turn has run. */
export function burnRate(costUsd: number | undefined, startedAt: number, turns: number, now: number): string {
  if (costUsd === undefined || !turns || !startedAt) return ''
  // ponytail: a floor of one minute keeps the first seconds from reading as a huge hourly rate.
  const hours = Math.max((now - startedAt) / 3_600_000, 1 / 60)
  return `$${(costUsd / hours).toFixed(2)}/hr · $${(costUsd / turns).toFixed(2)}/turn`
}

// Fields before the path on each porcelain v2 entry line: `1` ordinary, `2` renamed/copied, `u` unmerged.
const PATH_FIELD: Record<string, number> = { '1': 8, '2': 9, u: 10 }

/** Branch, ahead/behind and the changed files from `git status --porcelain=v2 --branch`. */
export function parseGitStatus(out: string): GitInfo {
  const info: GitInfo = { branch: '', files: [] }
  for (const line of out.split('\n')) {
    if (line.startsWith('# branch.head ')) info.branch = line.slice(14)
    else if (line.startsWith('# branch.ab ')) {
      const [ahead, behind] = line.slice(12).split(' ')
      info.ahead = Math.abs(Number(ahead))
      info.behind = Math.abs(Number(behind))
    } else if (line.startsWith('? ')) info.files.push({ path: line.slice(2), status: '?' })
    else if (line[0]! in PATH_FIELD) {
      const fields = line.split(' ')
      // XY: the worktree letter when there is one, else the staged one. A rename's old path follows a tab.
      const xy = fields[1] ?? '..'
      const path = fields.slice(PATH_FIELD[line[0]!]).join(' ').split('\t')[0] ?? ''
      info.files.push({ path, status: line[0] === 'u' ? 'U' : xy[1] !== '.' ? xy[1]! : xy[0]! })
    }
  }
  return info
}

/** `git diff --numstat` as path → lines added and deleted; binary files (`-`) are left out. */
export function parseNumstat(out: string): DiffStat {
  const stat: DiffStat = {}
  for (const line of out.split('\n')) {
    const [add, del, path] = line.split('\t')
    if (path && add !== '-' && del !== '-') stat[path] = { add: Number(add), del: Number(del) }
  }
  return stat
}

/** Runs git in the session's directory; undefined when git is missing, fails, or this is not a repo. */
async function runGit<T>($: EngineInterface, args: string[], parse: (out: string) => T): Promise<T | undefined> {
  try {
    const ran = await $.process.run(['git', ...args], { timeoutMs: 4000 })
    return ran.exitCode === 0 ? parse(ran.stdout) : undefined
  } catch {
    return undefined
  }
}

async function changeSettings($: EngineInterface, change: (s: Settings) => Settings) {
  await $.store.set('settings', await update($, settings, change))
}

async function copy($: EngineInterface, text: string, surface: RenderSurface) {
  const copied = await $.ui.copy({ text, surface })
  $.ui.toast(copied.isCopied ? 'Copied' : `Copy failed: ${copied.reason}`)
}

export function bar(percent: number, width = 20): string {
  const filled = Math.round((Math.min(100, Math.max(0, percent)) / 100) * width)
  return '━'.repeat(filled) + '─'.repeat(width - filled)
}

/** An Activity row's tool color by kind: shell, read, edit, MCP (`server/tool`), else agents and the rest. */
export function toolColor(tool: string): ThemeKey {
  if (tool === 'Bash') return SHELL
  if (READ_TOOLS.includes(tool)) return LINK
  if (EDIT_TOOLS.includes(tool)) return ADDED
  return tool.includes('/') ? IDE : PLAN
}

/** A token count's color by size: under 1k quiet, under 10k warm, above hot. */
export function tokenColor(tokens: number): ThemeKey {
  return tokens < 1000 ? TRACK : tokens < 10000 ? WARN : BAD
}

/** Session length as `45m`, or `2h40m` from an hour on. */
export function duration(minutes: number): string {
  return minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h${minutes % 60}m`
}

function tone(percent: number): ThemeKey {
  return percent >= 80 ? BAD : percent >= 40 ? WARN : OK
}

/** The first `used` of `width` cells as runs of [count, tone], each cell toned by where it sits. */
export function fillRuns(used: number, width: number): [number, ThemeKey][] {
  const runs: [number, ThemeKey][] = []
  for (let i = 0; i < used; i++) {
    const color = tone(((i + 1) / width) * 100)
    const last = runs.at(-1)
    if (last?.[1] === color) last[0]++
    else runs.push([1, color])
  }
  return runs
}

const LIMIT_LABELS: Record<string, string> = { five_hour: '5h', seven_day: '7d', spend_limit: '$' }

export function limitLabel(kind: string): string {
  return LIMIT_LABELS[kind] ?? kind.slice(0, 3)
}

/** Like `fit`, but keeps the end: a path's file name matters more than its first directories. */
export function fitStart(text: string, columns: number): string {
  return text.length <= columns ? text : `…${text.slice(text.length - Math.max(0, columns - 1))}`
}

export function fit(text: string, columns: number): string {
  return text.length <= columns ? text : `${text.slice(0, Math.max(0, columns - 1))}…`
}

/** Nerd Font progress glyphs: a filled or empty left cap, middles and right cap, one per cell. */
export function pillGlyphs(used: number, width: number): string {
  return Array.from({ length: width }, (_, i) => {
    const full = i < used
    if (i === 0) return full ? '\uee03' : '\uee00'
    if (i === width - 1) return full ? '\uee05' : '\uee02'
    return full ? '\uee04' : '\uee01'
  }).join('')
}

/**
 * A bar of `width` cells at `percent`, as [filled, track]: one character per cell, so a cell's
 * index is its offset. `smooth` fills in eighths of a cell, its partial cell counted as filled.
 */
export function barCells(percent: number, width: number, style: Look['barStyle']): [string, string] {
  const p = Math.min(100, Math.max(0, percent)) / 100
  if (style === 'smooth') {
    const eighths = Math.round(p * width * 8)
    const fill = '█'.repeat(Math.floor(eighths / 8)) + (eighths % 8 ? EIGHTHS[eighths % 8] : '')
    return [fill, ' '.repeat(width - fill.length)]
  }
  const used = Math.round(p * width)
  if (style === 'nerd') {
    const cells = pillGlyphs(used, width)
    return [cells.slice(0, used), cells.slice(used)]
  }
  const [full, empty] = BAR_CHARS[style]
  return [full.repeat(used), empty.repeat(width - used)]
}

/** The filled cells as runs of [count, color]: toned along the bar, by the whole value, or the accent. */
export function colorRuns(used: number, width: number, percent: number, mode: Look['barColor']): [number, ThemeKey][] {
  if (!used) return []
  if (mode === 'gradient') return fillRuns(used, width)
  return [[used, mode === 'solid' ? tone(percent) : ACCENT]]
}

/** The spinner's frame at `now`, or `still` when spinners are off. */
export function spinFrame(style: Look['spinner'], now: number, still: string): string {
  if (style === 'off') return still
  const frames = SPINNERS[style]
  return frames[Math.floor(now / FRAME_MS) % frames.length]!
}

/** A Nerd Font icon for a tool by kind, '' for any other icon set. */
export function toolIcon(tool: string, glyphs: Look['glyphs']): string {
  if (glyphs !== 'nerd') return ''
  if (tool === 'Bash') return '\uf489 '
  if (tool === 'Read') return '\uf15c '
  if (tool === 'Grep' || tool === 'Glob') return '\uf002 '
  if (tool.startsWith('Web')) return '\uf0ac '
  if (EDIT_TOOLS.includes(tool)) return '\uf040 '
  if (tool.includes('/')) return '\uf1e6 '
  return tool === 'Agent' || tool === 'Task' ? '\uf0c0 ' : '\uf0ad '
}

// Where each bar is gliding: the module's own memory, so a reload starts the bars over.
const glides = new Map<string, { from: number; to: number; at: number }>()
let gliding = false

/** The value bar `key` shows at `now` while it eases from its last value to `target`; first drawn, it fills from 0. */
export function glide(key: string, target: number, now: number, on: boolean): number {
  if (!on) return target
  const g = glides.get(key)
  const at = (x: { from: number; to: number; at: number }) => {
    const t = Math.min(1, Math.max(0, (now - x.at) / GLIDE_MS))
    return x.from + (x.to - x.from) * (1 - (1 - t) ** 3)
  }
  if (!g || g.to !== target) glides.set(key, { from: g ? at(g) : 0, to: target, at: now })
  const shown = at(glides.get(key)!)
  if (shown !== target) gliding = true
  return shown
}

export function compact(tokens: number): string {
  if (tokens >= 1_000_000) return `${+(tokens / 1_000_000).toFixed(1)}M`
  if (tokens >= 10000) return `${Math.round(tokens / 1000)}k`
  return tokens >= 1000 ? `${(tokens / 1000).toFixed(1)}k` : String(tokens)
}

async function refresh($: EngineInterface) {
  const [u, model, turns, agentList, tools, commands, local, gitInfo, stat] = await Promise.all([
    $.session.usage(),
    $.session.model(),
    $.session.turns(),
    $.agent.list(),
    $.tool.list(),
    $.command.list(),
    localCommands($).catch(() => ({})),
    runGit($, ['status', '--porcelain=v2', '--branch'], parseGitStatus),
    // Against HEAD, so staged edits count too; a repo with no commit yet reads as empty.
    runGit($, ['diff', '--numstat', 'HEAD'], parseNumstat),
  ])
  // Write only on change, so the timer doesn't redraw an idle pane.
  const nextUsage: Usage = {
    model,
    turns,
    startedAt: u.startedAt,
    contextPercent: u.context.percent,
    contextTokens: u.context.tokens,
    contextWindow: u.context.window,
    rateLimits: u.rateLimits,
    costUsd: u.cost?.usd,
  }
  const nextAgents: Agent[] = agentList.map(a => ({
    id: a.id,
    type: a.type,
    description: a.description,
    status: a.status,
  }))
  const nextMcp = mcpServers(tools.filter(t => t.mcp).map(t => t.name))
  if (!same(await read($, usage), nextUsage)) await update($, usage, () => nextUsage)
  if (!same(await read($, agents), nextAgents)) await update($, agents, () => nextAgents)
  if (!same(await read($, mcp), nextMcp)) await update($, mcp, () => nextMcp)
  const nextSkills = skillGroups(commands, local)
  if (!same(await read($, skills), nextSkills)) await update($, skills, () => nextSkills)
  const nextGit = gitInfo ?? null
  const nextStat = stat ?? {}
  if (!same(await read($, git), nextGit)) await update($, git, () => nextGit)
  if (!same(await read($, diffStat), nextStat)) await update($, diffStat, () => nextStat)
}

async function setPrompts($: EngineInterface, change: (list: QuickPrompt[]) => QuickPrompt[]) {
  await changeSettings($, x => ({ ...x, prompts: change(x.prompts) }))
}

/** Saves the open form over prompt `at`, or as a new one when `at` is -1, then closes it. */
async function saveDraft($: EngineInterface) {
  const d = await read($, promptDraft)
  if (!d) return
  const qp = { label: d.label.trim(), text: d.text.trim() }
  if (!qp.label || !qp.text) return void $.ui.toast('A prompt needs a name and a text')
  await setPrompts($, list => (d.at < 0 ? [...list, qp] : list.map((old, j) => (j === d.at ? qp : old))))
  await update($, promptDraft, () => null)
}

async function fillPrompt($: EngineInterface, text: string) {
  const filled = await $.prompt.fill({ text, mode: 'replace' })
  if (!filled.isFilled) $.ui.toast(`Prompt not filled${filled.refusal ? `: ${filled.refusal}` : ''}`)
}

// Set by each drawing: whether it holds a spinner or a gliding bar, so the frame clock redraws it.
let animating = false

async function toggle($: EngineInterface): Promise<string> {
  const open = (await $.ui.panes()).some(p => p.id === PANE)
  if (open) {
    await $.ui.close({ id: PANE })
    return 'Sidebar closed.'
  }
  await refresh($)
  await $.ui.open({ id: PANE, title: 'Sidebar', columns: 44 })
  return 'Sidebar opened.'
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'sidebar', description: 'Toggle the session sidebar' })
    const saved = loadSettings(await $.store.get('settings'))
    await update($, settings, () => saved)
    // Rows kept in $.state by an older version lack fields the pane reads; drop them.
    await update($, activity, list => list.filter(a => typeof a.id === 'string' && typeof a.tokens === 'number'))
    $.clock.every(REFRESH_MS, () => {
      void refresh($)
    })
    // Spinners and gliding bars: redraw on the frame clock, only while the last drawing moved.
    // ponytail: the flag stays set if the pane closes mid-motion; an invalidate with no pane is a no-op.
    $.clock.every(FRAME_MS, () => {
      if (animating) $.ui.invalidate('ui.render')
    })
    return next(e)
  })

  on('command.run', { command: 'sidebar' }, async $ => ({ text: await toggle($) }))

  on('session.measure', async ($, e, next) => {
    await update($, usage, u => ({
      ...u,
      contextPercent: e.context.percent,
      contextTokens: e.context.tokens,
      contextWindow: e.context.window,
      rateLimits: e.rateLimits,
      costUsd: e.cost?.usd,
    }))
    return next(e)
  })

  on('tool.call', async ($, e, next) => {
    const started = await $.clock.now()
    const tool = String(e.tool)
    const args = Object.fromEntries(Object.entries(e).filter(([k]) => !RESERVED.includes(k)))
    const described = describeCall(tool, args)
    const id = e.tool_use_id
    await update($, running, list => [...list, { id, tool: described.tool, target: described.target, agent: !!e.agentId, startedAt: started }])
    let ran: Awaited<ReturnType<typeof next>>
    try {
      ran = await next(e)
    } finally {
      await update($, running, list => list.filter(r => r.id !== id))
    }
    const ms = (await $.clock.now()) - started
    const failed = ran.deny !== undefined || ran.isError === true
    const tokens = estimateTokens(args, ran.text)
    const call: Activity = {
      id: e.tool_use_id,
      ...described,
      agent: !!e.agentId,
      failed,
      ms,
      tokens,
      count: 1,
    }
    await update($, toolCalls, n => n + 1)
    await update($, activity, list => pushActivity(list, call))
    if (failed) {
      const row: ErrorRow = {
        id: e.tool_use_id,
        tool: described.tool,
        target: described.target,
        text: (ran.deny ?? ran.text ?? '').slice(0, 500),
      }
      await update($, errors, list => [...list, row].slice(-KEEP_ERRORS))
    }

    const server = mcpServerOf(tool)
    if (server !== undefined) {
      await update($, mcpUsage, all => ({
        ...all,
        [server]: { calls: (all[server]?.calls ?? 0) + 1, tokens: (all[server]?.tokens ?? 0) + tokens },
      }))
    }
    if (failed || e.agentId) return ran

    if (e.tool === 'TodoWrite') {
      const list = (ran.result as { newTodos?: Omit<Todo, 'id'>[] } | undefined)?.newTodos ?? []
      await update($, todos, () => list.map((t, i) => ({ id: String(i), content: t.content, status: t.status })))
    } else if (e.tool === 'TaskCreate') {
      const task = (ran.result as { task?: { id: string; subject: string } } | undefined)?.task
      if (task) await update($, todos, list => [...list, { id: task.id, content: task.subject, status: 'pending' as const }])
    } else if (e.tool === 'TaskUpdate') {
      const { taskId, status, subject } = e as unknown as { taskId: string; status?: string; subject?: string }
      await update($, todos, list =>
        status === 'deleted'
          ? list.filter(t => t.id !== taskId)
          : list.map((t): Todo =>
              t.id === taskId
                ? { ...t, content: subject ?? t.content, status: (status as Todo['status']) ?? t.status }
                : t,
            ),
      )
    }
    return ran
  }).catch(($, e, next) => next(e))

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const elements = $.ui.resolve(e)
    const { Box, Button, Text } = elements
    // Every surface but mobile draws an Input.
    const Input = 'Input' in elements ? elements.Input : undefined
    const [u, todoList, agentList, servers, skillList, serverUse, recent, calls, s, shown, open, editing, draft, repo, stat, errs, inFlight] =
      await Promise.all([
      read($, usage),
      read($, todos),
      read($, agents),
      read($, mcp),
      read($, skills),
      read($, mcpUsage),
      read($, activity),
      read($, toolCalls),
      read($, settings),
      read($, view),
      read($, expanded),
      read($, editingPrompts),
      read($, promptDraft),
      read($, git),
      read($, diffStat),
      read($, errors),
      read($, running),
    ])
    const now = await $.clock.now()
    const L = s.look
    const G = GLYPHS[L.glyphs]
    const spin = (still: string) => spinFrame(L.spinner, now, still)
    gliding = false
    // One meter row: 4-col label, bar, ' 100%' (always 5 wide), and ' ↻ Ddd HH:MM' when a limit resets.
    // Keep it inside the dock so it never wraps.
    const resetCols = u.rateLimits.some(r => r.resetsAt) ? 12 : 0
    const fitWidth = Math.max(6, Math.min(24, (e.props.bodyColumns ?? 44) - 2 - 4 - 6 - resetCols))
    const width = L.barWidth === 'fit' ? fitWidth : Math.min(Number(L.barWidth), fitWidth)
    const rowColumns = (e.props.bodyColumns ?? 44) - 2
    // A bar in the chosen style, its filled cells colored per `barColor`: `gradient` tones each
    // cell by where it sits, so the bar turns green to yellow to red along its length.
    // `smooth` lays its eighth blocks over a track background; `nerd` uses the font's progress
    // glyphs (U+EE00-EE05), a slim rounded pill.
    const pill = (percent: number, cols: number) => {
      const [fill, track] = barCells(percent, cols, L.barStyle)
      let at = 0
      const runs = colorRuns(fill.length, cols, percent, L.barColor).map(([n, color]) => (
        <Text color={color}>{fill.slice(at, (at += n))}</Text>
      ))
      return L.barStyle === 'smooth' ? (
        <Text backgroundColor={TRACK}>
          {runs}
          {track}
        </Text>
      ) : (
        <Text>
          {runs}
          {L.barStyle === 'nerd' ? <Text color={TRACK}>{track}</Text> : <Text dimColor>{track}</Text>}
        </Text>
      )
    }
    // Called as a function, not drawn as a tag, so its glide counts toward this drawing's motion.
    const meter = (label: string, percent: number, reset?: string) => {
      const shown = glide(label, percent, now, L.barMotion === 'on')
      return (
        <Text wrap="truncate">
          <Text dimColor>{label.padEnd(4)}</Text>
          {pill(shown, width)}
          {/* Padded to three digits so the bars and resets line up. */}
          <Text color={tone(shown)}>{` ${String(Math.round(shown)).padStart(3)}%`}</Text>
          {reset ? <Text color={TRACK}>{` ${G.reset} ${reset}`}</Text> : null}
        </Text>
      )
    }
    // A pressable row fitted to the pane: a marker (`✗ `, `↳ `), name, dim target, dot leader,
    // then right-hand parts (a part with no color draws dim). A Button doesn't truncate, so it
    // is fitted by hand.
    const LeaderRow = (p: {
      k: string
      mark?: [text: string, color: ThemeKey]
      name: string
      target?: string
      color?: ThemeKey
      right: [text: string, color?: ThemeKey][]
      onPress: (press: { surface: RenderSurface }) => unknown
    }) => {
      const mark = p.mark?.[0] ?? ''
      const rightText = p.right.map(([t]) => t).join(' ')
      const room = rowColumns - (rightText ? rightText.length + 3 : 0)
      const left = fit(`${mark}${p.name}${p.target ? ` ${p.target}` : ''}`, room)
      const nameEnd = mark.length + p.name.length
      const dots = '·'.repeat(Math.max(1, rowColumns - left.length - rightText.length - 2))
      return (
        <Button key={p.k} plain onPress={p.onPress}>
          {mark ? <Text color={p.mark?.[1]}>{left.slice(0, mark.length)}</Text> : null}
          <Text color={p.color}>{left.slice(mark.length, nameEnd)}</Text>
          <Text dimColor>{left.slice(nameEnd)}</Text>
          {rightText ? <Text color={TRACK}>{` ${dots} `}</Text> : null}
          {p.right.map(([t, c], i) => (
            <Text color={c} dimColor={!c}>{i ? ` ${t}` : t}</Text>
          ))}
        </Button>
      )
    }
    const minutes = u.startedAt ? Math.floor((now - u.startedAt) / 60000) : 0
    // Burn rate's `$` values at full strength, the units between them dim.
    const burn = burnRate(u.costUsd, u.startedAt, u.turns, now)
    const burnParts = burn ? ` · ${burn}`.split(/(\$[\d.]+)/) : []
    const doneCount = todoList.filter(t => t.status === 'completed').length
    const live = agentList.filter(a => a.status === 'running' || a.status === 'pending' || a.status === 'waiting')

    const usedTokens = Object.values(serverUse).reduce((sum, x) => sum + x.tokens, 0)
    const byUse = [...servers].sort(
      (a, b) => (serverUse[b.name]?.tokens ?? -1) - (serverUse[a.name]?.tokens ?? -1) || a.name.localeCompare(b.name),
    )
    // Known up front, so a section can tell whether the one above it is drawn and open.
    const empty: Record<SectionId, boolean> = {
      usage: false,
      git: !repo,
      // Never empty: it always holds its edit control.
      prompts: false,
      skills: skillList.length === 0,
      todos: todoList.length === 0,
      agents: agentList.length === 0,
      mcp: servers.length === 0,
      activity: recent.length === 0 && inFlight.length === 0,
      errors: errs.length === 0,
    }
    // `auto` hides an empty section, header and all; `show` keeps it with its placeholder.
    const drawn = SECTION_IDS.filter(id => s.sections[id] === 'show' || (s.sections[id] === 'auto' && !empty[id]))
    const isOpen = (id: SectionId) => !s.collapsed.includes(id)

    // A header is a rule: caret, name and title in the section's color, then a line to the edge, ending in `summary` while collapsed. Fitted by hand, as a Button.
    const Section = (p: { id: SectionId; title?: string; summary?: string; none?: string; children?: JSX.Children }) => {
      const at = drawn.indexOf(p.id)
      if (at < 0) return null
      const open = isOpen(p.id)
      const above = drawn[at - 1]
      // Collapsed headers stack tight; anything else keeps a blank row above.
      // The first section sits right under the pane's title row.
      const gap = above === undefined || L.density === 'compact' ? 0 : open || isOpen(above) ? 1 : 0
      const toggleOpen = () =>
        changeSettings($, x => ({
          ...x,
          collapsed: x.collapsed.includes(p.id) ? x.collapsed.filter(c => c !== p.id) : [...x.collapsed, p.id],
        }))
      const name = `${open ? G.open : G.closed} ${TITLES[p.id]}`
      const tail = !open && p.summary ? ` ${fit(p.summary, 16)}` : ''
      const label = fit(p.title ? `${name} ${p.title}` : name, rowColumns - 2 - tail.length)
      const rule = ` ${HEADER_RULES[L.headerStyle].repeat(Math.max(0, rowColumns - label.length - 1 - tail.length))}`
      return (
        <Box flexDirection="column" marginTop={gap}>
          <Button key={`hdr:${p.id}`} plain onPress={toggleOpen}>
            <Text bold color={ACCENT}>{label}</Text>
            <Text color={TRACK}>{rule}</Text>
            {tail ? <Text dimColor>{tail}</Text> : null}
          </Button>
          {open && (empty[p.id] ? <Text dimColor>{p.none}</Text> : p.children)}
        </Box>
      )
    }

    const SettingsView = () => (
      <Box flexDirection="column">
        <Text bold color={ACCENT}>Sections</Text>
        <Text dimColor>auto = hide when empty</Text>
        {SECTION_IDS.map(id => (
          <Button
            key={`set:${id}`}
            plain
            onPress={() =>
              changeSettings($, x => ({ ...x, sections: { ...x.sections, [id]: cycle(VISIBILITY, x.sections[id]) } }))
            }
          >
            <Text color={ACCENT}>{TITLES[id].padEnd(13)}</Text>
            <Text color={VISIBILITY_COLORS[s.sections[id]]}>{s.sections[id]}</Text>
          </Button>
        ))}
        <Button
          key="set:recent"
          plain
          label={`${'Recent rows'.padEnd(13)}${s.recentCount}`}
          onPress={() => changeSettings($, x => ({ ...x, recentCount: cycle(RECENT_COUNTS, x.recentCount) }))}
        />
        <Box marginTop={1}>
          <Text bold color={ACCENT}>Look</Text>
        </Box>
        <Text dimColor>nerd needs a Nerd Font</Text>
        {LOOK_KEYS.map(k => (
          <Button
            key={`look:${k}`}
            plain
            onPress={() =>
              changeSettings($, x => ({ ...x, look: { ...x.look, [k]: cycle(LOOK[k].values as string[], x.look[k]) } }))
            }
          >
            <Text color={ACCENT}>{LOOK[k].label.padEnd(13)}</Text>
            <Text color={TEXT}>{L[k].padEnd(10)}</Text>
            {/* A sample of what the value draws. */}
            {k === 'barStyle' || k === 'barColor' ? pill(70, 8) : null}
            {k === 'glyphs' ? <Text dimColor>{[G.open, G.done, G.fail, G.live, G.reset].join(' ')}</Text> : null}
            {k === 'spinner' ? <Text color={WARN}>{spin('·')}</Text> : null}
            {k === 'headerStyle' ? <Text color={TRACK}>{HEADER_RULES[L.headerStyle].repeat(8)}</Text> : null}
          </Button>
        ))}
        <Box marginTop={1}>
          <Button key="done" label="Done" onPress={() => update($, view, () => 'main')} />
        </Box>
      </Box>
    )

    const tree = (
      <Box flexDirection="column" paddingX={1}>
        {shown === 'settings' ? (
          <SettingsView />
        ) : (
          <Box flexDirection="column">
            <Section
              id="usage"
              summary={[
                u.contextPercent === undefined ? '' : `${Math.round(u.contextPercent)}%`,
                u.costUsd === undefined ? '' : `$${u.costUsd.toFixed(2)}`,
              ]
                .filter(Boolean)
                .join(' · ')}
            >
              {/* Always a bar: no reading yet, or just compacted, draws it empty. */}
              {meter('ctx', u.contextPercent ?? 0)}
              {u.rateLimits.map(r => meter(limitLabel(r.kind), r.percentUsed, r.resetsAt ? resetLabel(r.resetsAt, now) : undefined))}
              <Text wrap="truncate">
                <Text color={TEXT}>{`${compact(u.contextTokens ?? 0)} / ${compact(u.contextWindow)}`}</Text>
                <Text dimColor> tokens</Text>
              </Text>
              {u.costUsd === undefined ? null : (
                <Text wrap="truncate">
                  <Text bold color={TEXT}>{`$${u.costUsd.toFixed(2)}`}</Text>
                  {burnParts.map((t, i) => (!t ? null : i % 2 ? <Text color={TEXT}>{t}</Text> : <Text dimColor>{t}</Text>))}
                </Text>
              )}
              <Text wrap="truncate">
                <Text color={TEXT}>{String(u.turns)}</Text>
                <Text dimColor> turns · </Text>
                <Text color={TEXT}>{String(calls)}</Text>
                <Text dimColor> calls · </Text>
                <Text color={TEXT}>{duration(minutes)}</Text>
              </Text>
            </Section>

            <Section id="git" summary={repo?.branch} none="Not a git repo">
              {repo && (
                <Button key="git:branch" plain onPress={press => copy($, repo.branch, press.surface)}>
                  <Text wrap="truncate">
                    <Text bold color={MERGED}>{`${G.branch}${repo.branch}`}</Text>
                    {repo.ahead === undefined ? null : (
                      <Text color={repo.ahead ? OK : undefined} dimColor={!repo.ahead}>{` ↑${repo.ahead}`}</Text>
                    )}
                    {repo.behind === undefined ? null : (
                      <Text color={repo.behind ? WARN : undefined} dimColor={!repo.behind}>{` ↓${repo.behind}`}</Text>
                    )}
                    <Text dimColor> · </Text>
                    {repo.files.length ? (
                      <Text color={WARN}>{`${repo.files.length} changed`}</Text>
                    ) : (
                      <Text dimColor>clean</Text>
                    )}
                  </Text>
                </Button>
              )}
              {repo?.files.slice(0, SHOW_GIT_FILES).map(f => {
                const d = stat[f.path]
                const right: [string, ThemeKey?][] = d
                  ? [[`+${d.add}`, ADDED], [`−${d.del}`, REMOVED]]
                  : [[GIT_LABELS[f.status] ?? f.status, GIT_COLORS[f.status]]]
                const room = rowColumns - right.map(([t]) => t).join(' ').length - 3
                return (
                  <LeaderRow
                    k={`git:${f.path}`}
                    name={fitStart(f.path, room)}
                    color={GIT_COLORS[f.status] ?? TEXT}
                    right={right}
                    onPress={press => copy($, f.path, press.surface)}
                  />
                )
              })}
              {repo && repo.files.length > SHOW_GIT_FILES && (
                <Text dimColor>{`+${repo.files.length - SHOW_GIT_FILES} more`}</Text>
              )}
            </Section>

            <Section id="prompts" summary={String(s.prompts.length)}>
              {draft && Input ? (
                // One prompt's form: a field for its name and one for the text it puts in the prompt box.
                <Box flexDirection="column">
                  <Text dimColor>{draft.at < 0 ? 'New prompt' : `Editing "${s.prompts[draft.at]?.label ?? ''}"`}</Text>
                  <Input
                    key="qp:name"
                    label="Name   "
                    placeholder="commit"
                    value={draft.label}
                    autoFocus
                    onInput={label => update($, promptDraft, d => d && { ...d, label })}
                    onSubmit={async label => {
                      // Enter carries the field's text; keep it before saving.
                      await update($, promptDraft, d => d && { ...d, label })
                      await saveDraft($)
                    }}
                  />
                  <Input
                    key="qp:text"
                    label="Prompt "
                    placeholder="commit the changes"
                    value={draft.text}
                    onInput={text => update($, promptDraft, d => d && { ...d, text })}
                    onSubmit={async text => {
                      // Enter carries the field's text; keep it before saving.
                      await update($, promptDraft, d => d && { ...d, text })
                      await saveDraft($)
                    }}
                  />
                  <Box columnGap={2}>
                    <Button key="qp:save" plain onPress={() => saveDraft($)}>
                      <Text color={OK}>save</Text>
                    </Button>
                    <Button key="qp:cancel" plain dimColor onPress={() => update($, promptDraft, () => null)}>
                      cancel
                    </Button>
                  </Box>
                </Box>
              ) : editing ? (
                // The list: each prompt with its own edit and delete, then add and done.
                <Box flexDirection="column">
                  {s.prompts.length === 0 && <Text dimColor>No prompts yet</Text>}
                  {s.prompts.map((qp, i) => (
                    <Box justifyContent="space-between">
                      <Text wrap="truncate">
                        <Text color={LINK}>{qp.label}</Text>
                        <Text dimColor>{` ${fit(qp.text, Math.max(0, rowColumns - qp.label.length - 14))}`}</Text>
                      </Text>
                      <Box columnGap={1}>
                        {Input ? (
                          <Button key={`qp:change:${i}`} plain onPress={() => update($, promptDraft, () => ({ at: i, ...qp }))}>
                            <Text color={LINK}>edit</Text>
                          </Button>
                        ) : null}
                        <Button key={`qp:del:${i}`} plain onPress={() => setPrompts($, list => list.filter((_, j) => j !== i))}>
                          <Text color={BAD}>delete</Text>
                        </Button>
                      </Box>
                    </Box>
                  ))}
                  <Box columnGap={2} marginTop={1}>
                    {Input ? (
                      <Button key="qp:new" plain onPress={() => update($, promptDraft, () => ({ at: -1, label: '', text: '' }))}>
                        <Text color={OK}>+ new prompt</Text>
                      </Button>
                    ) : (
                      <Text dimColor>Adding needs a keyboard</Text>
                    )}
                    <Button key="qp:done" plain dimColor onPress={() => update($, editingPrompts, () => false)}>
                      done
                    </Button>
                  </Box>
                </Box>
              ) : (
                <Box flexWrap="wrap" columnGap={2}>
                  {s.prompts.map((qp, i) => (
                    <Button key={`qp:${i}`} plain onPress={() => fillPrompt($, qp.text)}>
                      <Text color={LINK}>{qp.label}</Text>
                    </Button>
                  ))}
                  <Button key="qp:edit" plain dimColor onPress={() => update($, editingPrompts, () => true)}>
                    {s.prompts.length ? 'manage' : '+ add a prompt'}
                  </Button>
                </Box>
              )}
            </Section>

            <Section id="skills" summary={String(skillList.reduce((n, g) => n + g.names.length, 0))} none="None">
              {skillList.map(g => {
                const isOpen = s.openSkillGroups.includes(g.group)
                return (
                <Box flexDirection="column">
                  {/* Groups start collapsed; opening one is remembered across sessions. */}
                  <Button
                    key={`skillgroup:${g.group}`}
                    plain
                    onPress={() =>
                      changeSettings($, x => ({
                        ...x,
                        openSkillGroups: x.openSkillGroups.includes(g.group)
                          ? x.openSkillGroups.filter(o => o !== g.group)
                          : [...x.openSkillGroups, g.group],
                      }))
                    }
                  >
                    <Text dimColor>{`${isOpen ? G.open : G.closed} `}</Text>
                    <Text color={TEXT}>{g.group}</Text>
                    <Text dimColor>{` ${g.names.length}`}</Text>
                  </Button>
                  {isOpen && (
                  <Box flexWrap="wrap" columnGap={2} paddingLeft={2}>
                    {g.names.map(n => (
                      // Pressing one puts `/name ` in the prompt box, ready for arguments.
                      <Button key={`skill:${n}`} plain onPress={() => fillPrompt($, `/${n} `)}>
                        {/* The group already names the package, so the chip drops a `package:` prefix. */}
                        <Text color={LINK}>{chipName(n)}</Text>
                      </Button>
                    ))}
                  </Box>
                  )}
                </Box>
                )
              })}
            </Section>

            <Section id="todos" title={todoList.length ? `${doneCount}/${todoList.length}` : ''} none="None">
              {todoList.map(t => {
                const done = t.status === 'completed'
                const doing = t.status === 'in_progress'
                return (
                  <Text wrap="truncate" strikethrough={done} color={done ? TRACK : doing ? WARN : TEXT}>
                    {done ? G.done : doing ? spin(G.doing) : G.todo} {t.content}
                  </Text>
                )
              })}
            </Section>

            <Section
              id="agents"
              title={live.length ? `(${live.length} live)` : ''}
              summary={String(agentList.length)}
              none="None this session"
            >
              {agentList.slice(-8).map(a => {
                const isLive = live.includes(a)
                return (
                  <Text wrap="truncate">
                    <Text color={a.status === 'failed' || a.status === 'killed' ? BAD : isLive ? WARN : OK}>
                      {isLive ? spin(G.live) : a.status === 'completed' ? G.done : G.fail}
                    </Text>
                    <Text color={TEXT}>{` ${a.type}`}</Text>
                    <Text dimColor>{`: ${a.description}`}</Text>
                  </Text>
                )
              })}
            </Section>

            <Section
              id="mcp"
              title={`(${servers.length})${usedTokens ? ` ~${compact(usedTokens)} tok` : ''}`}
              none="None connected"
            >
              {byUse.map(srv => {
                const use = serverUse[srv.name]
                return (
                  <Text wrap="truncate">
                    <Text color={OK}>{G.server}</Text>
                    <Text color={TEXT}>{` ${srv.name} `}</Text>
                    <Text dimColor>{`${srv.tools} tools${use ? ` · ${use.calls} calls ` : ''}`}</Text>
                    {use ? <Text color={tokenColor(use.tokens)}>{`~${compact(use.tokens)} tok`}</Text> : null}
                  </Text>
                )
              })}
            </Section>

            <Section id="activity" none="None yet">
              {inFlight
                .slice()
                .reverse()
                .map(r => (
                  <LeaderRow
                    k={`run:${r.id}`}
                    mark={[`${spin(G.live)} ${r.agent ? `${G.sub} ` : ''}`, WARN]}
                    name={`${toolIcon(r.tool, L.glyphs)}${r.tool}`}
                    target={r.target}
                    color={toolColor(r.tool)}
                    right={[[`${((now - r.startedAt) / 1000).toFixed(1)}s`, WARN]]}
                    onPress={() => undefined}
                  />
                ))}
              {recent
                .slice(-s.recentCount)
                .reverse()
                .map(a => {
                  const isOpen = open === a.id
                  const mark = `${a.failed ? `${G.fail} ` : ''}${a.agent ? `${G.sub} ` : ''}`
                  return (
                    <Box flexDirection="column">
                      <LeaderRow
                        k={`act:${a.id}`}
                        mark={mark ? [mark, a.failed ? BAD : TRACK] : undefined}
                        name={`${toolIcon(a.tool, L.glyphs)}${a.tool}${a.count > 1 ? ` ×${a.count}` : ''}`}
                        target={a.target}
                        color={a.failed ? BAD : toolColor(a.tool)}
                        right={[[compact(a.tokens), tokenColor(a.tokens)]]}
                        onPress={() => update($, expanded, id => (id === a.id ? '' : a.id))}
                      />
                      {isOpen && (
                        <Box flexDirection="column" paddingLeft={2}>
                          {a.full && <Text dimColor>{a.full}</Text>}
                          <Text dimColor>
                            {`${(a.ms / 1000).toFixed(1)}s · ~${compact(a.tokens)} tok${a.count > 1 ? ` · ${a.count} calls` : ''}`}
                          </Text>
                          {a.full && (
                            <Button key={`copy:${a.id}`} plain dimColor label="copy" onPress={press => copy($, a.full, press.surface)} />
                          )}
                        </Box>
                      )}
                    </Box>
                  )
                })}
            </Section>

            <Section id="errors" title={errs.length ? `(${errs.length})` : ''} none="None">
              {errs
                .slice()
                .reverse()
                .map(x => {
                  const key = `err:${x.id}`
                  return (
                    <Box flexDirection="column">
                      <LeaderRow
                        k={key}
                        mark={[`${G.fail} `, BAD]}
                        name={x.tool}
                        target={x.target}
                        color={BAD}
                        right={[]}
                        onPress={() => update($, expanded, id => (id === key ? '' : key))}
                      />
                      {open === key && (
                        <Box flexDirection="column" paddingLeft={2}>
                          <Text dimColor>{x.text || 'no message'}</Text>
                          {x.text && (
                            <Button key={`errcopy:${x.id}`} plain dimColor label="copy" onPress={press => copy($, x.text, press.surface)} />
                          )}
                        </Box>
                      )}
                    </Box>
                  )
                })}
            </Section>

            <Box marginTop={1}>
              <Button key="gear" plain dimColor label={`${G.gear} settings`} onPress={() => update($, view, () => 'settings')} />
            </Box>
          </Box>
        )}
      </Box>
    )
    const spinning = live.length > 0 || todoList.some(t => t.status === 'in_progress')
    // A running call's timer ticks even with spinners off.
    // The settings view animates its spinner sample.
    animating = gliding || inFlight.length > 0 || (L.spinner !== 'off' && (spinning || shown === 'settings'))
    return tree
  })
}
