export type Todo = { id: string; content: string; status: 'pending' | 'in_progress' | 'completed' }
export type Agent = { id: string; type: string; description: string; status: string }
export type McpServer = { name: string; tools: number }
export type McpUsage = Record<string, { calls: number; tokens: number }>
export type RateLimit = { kind: string; percentUsed: number; resetsAt?: string }
export type Usage = {
  model: string
  turns: number
  startedAt: number
  contextPercent?: number
  contextTokens?: number
  contextWindow: number
  rateLimits: RateLimit[]
  costUsd?: number
}
// `target` is the short context shown on the row, `full` what a press copies.
export type Activity = {
  id: string
  tool: string
  target: string
  full: string
  agent: boolean
  failed: boolean
  ms: number
  tokens: number
  count: number
}
// `ahead`/`behind` are absent when the branch tracks no upstream.
// `status` is the porcelain letter: M, A, D, R, C, U, or ? for untracked.
export type GitFile = { path: string; status: string }
export type GitInfo = { branch: string; ahead?: number; behind?: number; files: GitFile[] }
export type ErrorRow = { id: string; tool: string; target: string; text: string }
export type QuickPrompt = { label: string; text: string }
// The prompt form while open: `at` is the index it edits, -1 for a new one.
export type PromptDraft = { at: number } & QuickPrompt
// Repo-relative path → lines added and deleted, as `git diff --numstat` reports them.
export type DiffStat = Record<string, { add: number; del: number }>
export type SectionId = 'usage' | 'git' | 'todos' | 'agents' | 'mcp' | 'prompts' | 'activity' | 'errors'
export type Visibility = 'show' | 'auto' | 'hide'
export type Settings = {
  sections: Record<SectionId, Visibility>
  collapsed: SectionId[]
  recentCount: number
  nerdFont: boolean
  prompts: QuickPrompt[]
}
// Inside the block below `Settings` names claude-code's own type, so refer to ours by another name.
export type SidebarSettings = Settings

declare module 'claude-code' {
  interface PluginState {
    sidebar: {
      usage: Usage
      todos: Todo[]
      agents: Agent[]
      mcp: McpServer[]
      mcpUsage: McpUsage
      activity: Activity[]
      git: GitInfo | null
      diffStat: DiffStat
      // `contextPercent` at each main-thread turn end, oldest first; reset by a compaction.
      // The context percent auto-compaction runs at.
      errors: ErrorRow[]
      toolCalls: number
      settings: SidebarSettings
      view: 'main' | 'settings'
      expanded: string
      editingPrompts: boolean
      promptDraft: PromptDraft | null
    }
  }
}
