/**
 * Pure state for the `/voice` console: the one big state word, the error
 * copy that replaces the hint (req #547), the tool strip and the transcript.
 * No React and no imports, so `node --test web/app/voice/voice-state.test.ts`
 * runs it directly.
 */

export type Problem = {
  kind: "budget" | "unconfigured" | "mic" | "connection" | "ended"
  /** The big state word above the mic. */
  state: string
  title: string
  body: string
  retry: boolean
  /** Budget only: seconds until the team's voice minutes come back (Retry-After). */
  retryAfterSeconds?: number
}

/** A session-start failure (thrown by createVoiceSession or getUserMedia) → copy. */
export function sessionProblem(err: unknown): Problem {
  const e = (err ?? {}) as { status?: unknown; retryAfterSeconds?: unknown; name?: unknown; message?: unknown }
  if (e.status === 429) {
    const secs = typeof e.retryAfterSeconds === "number" && e.retryAfterSeconds > 0 ? e.retryAfterSeconds : null
    const when = secs === null ? "Try again later." : `Try again in ${Math.max(1, Math.ceil(secs / 60))} min.`
    return {
      kind: "budget",
      state: "Out of voice minutes",
      title: "Your team has used its voice minutes for now.",
      body: `${when} Typing a call on Plays still works.`,
      retry: false,
      ...(secs === null ? {} : { retryAfterSeconds: secs }),
    }
  }
  if (e.status === 503) {
    return {
      kind: "unconfigured",
      state: "Voice unavailable",
      title: "Voice isn't set up on this server.",
      body: "Ask your admin to configure ElevenLabs.",
      retry: false,
    }
  }
  if (e.name === "NotAllowedError" || e.name === "SecurityError" || e.name === "NotFoundError") {
    return {
      kind: "mic",
      state: "Microphone blocked",
      title: "We can't hear you.",
      body: "Allow microphone access in your browser's address bar, then try again.",
      retry: true,
    }
  }
  const reason = typeof e.message === "string" && e.message ? e.message : "Something went wrong."
  return { kind: "connection", state: "Couldn't connect", title: "The call didn't start.", body: reason, retry: true }
}

type Disconnect = { reason: string; message?: string; closeCode?: number; closeReason?: string }

/** The SDK's disconnect details → copy; `null` when the coach hung up. */
export function disconnectProblem(details: Disconnect): Problem | null {
  if (details.reason === "user") return null
  const code = details.closeCode
    ? ` (code ${details.closeCode}${details.closeReason ? `: ${details.closeReason}` : ""})`
    : ""
  if (details.reason === "error") {
    return {
      kind: "connection",
      state: "Connection lost",
      title: "The call dropped.",
      body: `${details.message || "Unknown error"}${code}`,
      retry: true,
    }
  }
  return { kind: "ended", state: "Call ended", title: "The agent ended the call.", body: `Tap the mic to start again.${code}`, retry: false }
}

/** An SDK `onError` while connected → copy. */
export function errorProblem(message: string): Problem {
  return { kind: "connection", state: "Connection error", title: "Something went wrong on the call.", body: message, retry: true }
}

export type MicState = { word: string; tone: "idle" | "live" | "busy" | "error" }

export function micState(s: {
  status: string
  isSpeaking: boolean
  toolRunning: boolean
  starting: boolean
  problem: Problem | null
}): MicState {
  if (s.starting || s.status === "connecting") return { word: "Connecting…", tone: "busy" }
  if (s.status === "connected") {
    // A speaking agent wins: a lost tool response must not pin "Thinking…" over its voice.
    if (s.isSpeaking) return { word: "Speaking", tone: "live" }
    return { word: s.toolRunning ? "Thinking…" : "Listening", tone: "live" }
  }
  if (s.problem) return { word: s.problem.state, tone: s.problem.kind === "ended" ? "idle" : "error" }
  return { word: "Start call", tone: "idle" }
}

/** Ms until a problem stops disabling the mic: only a budget with a known Retry-After lifts. */
export function micUnblockMs(p: Problem | null): number | null {
  if (p?.kind !== "budget" || !p.retryAfterSeconds) return null
  // setTimeout fires at once past 2^31-1 ms (~24.8 days); a monthly cap can send ~31 days.
  return Math.min(p.retryAfterSeconds * 1000, MAX_TIMEOUT_MS)
}

const MAX_TIMEOUT_MS = 2_147_483_647

/** Whether the mic stays disabled; `waitedOut` is true once micUnblockMs has elapsed. */
export function micBlocked(p: Problem | null, waitedOut: boolean): boolean {
  if (p?.kind === "unconfigured") return true
  return p?.kind === "budget" && !waitedOut
}

export type ToolChip = { id: string; name: string; kind: "server" | "client"; state: "running" | "done" | "error" }

const MAX_CHIPS = 8

export function toolStarted(strip: ToolChip[], chip: Omit<ToolChip, "state">): ToolChip[] {
  if (strip.some((c) => c.id === chip.id)) return strip
  return [...strip, { ...chip, state: "running" as const }].slice(-MAX_CHIPS)
}

export function toolFinished(strip: ToolChip[], id: string, ok: boolean): ToolChip[] {
  return strip.map((c) => (c.id === id ? { ...c, state: ok ? "done" : "error" } : c))
}

/**
 * Server tool chips still running once the agent speaks or is interrupted: their
 * agent_tool_response was lost or will never come, so stop showing them as live.
 */
export function settleServerChips(strip: ToolChip[]): ToolChip[] {
  return strip.some((c) => c.kind === "server" && c.state === "running")
    ? strip.map((c) => (c.kind === "server" && c.state === "running" ? { ...c, state: "done" as const } : c))
    : strip
}

/** A tap this soon after the coach's last line or a finished tool call only arms the hang-up. */
export const ARM_WINDOW_MS = 1500
/** How long an armed hang-up waits for the second tap. */
export const ARMED_FOR_MS = 3000

/**
 * What a tap on the live call button does. While the coach's last line is still
 * unanswered (the agent is thinking or a tool is running), or right after the coach
 * spoke or a tool answered, a tap is likely push-to-talk habit, so it only arms
 * ("Tap again to end call"); a second tap while armed, or any other tap, ends it.
 */
export function endTapAction(s: {
  now: number
  lastUserLineAt: number | null
  lastAgentSpokeAt: number | null
  lastToolEventAt: number | null
  armedUntil: number | null
}): "end" | "arm" {
  if (s.armedUntil !== null && s.now < s.armedUntil) return "end"
  const unanswered =
    s.lastUserLineAt !== null && (s.lastAgentSpokeAt === null || s.lastAgentSpokeAt < s.lastUserLineAt)
  const recent = (t: number | null) => t !== null && s.now - t < ARM_WINDOW_MS
  return unanswered || recent(s.lastUserLineAt) || recent(s.lastToolEventAt) ? "arm" : "end"
}

/** `id` rises with each new line (a partial and the final that settles it share one); React key. */
export type Line = { id: number; role: "user" | "agent"; text: string; partial: boolean }

const MAX_LINES = 30

function withoutTrailingPartial(lines: Line[]): Line[] {
  return lines.at(-1)?.partial ? lines.slice(0, -1) : lines
}

function nextId(lines: Line[]): number {
  return (lines.at(-1)?.id ?? 0) + 1
}

/** A final transcript line; a final user line replaces the partial it settles. */
export function addFinalLine(lines: Line[], role: Line["role"], text: string): Line[] {
  if (!text.trim()) return lines
  const base = role === "user" ? withoutTrailingPartial(lines) : lines
  return [...base, { id: nextId(base), role, text, partial: false }].slice(-MAX_LINES)
}

/** The words heard so far in the coach's current turn, updated in place. */
export function setPartialLine(lines: Line[], text: string): Line[] {
  if (!text.trim()) return lines
  const base = withoutTrailingPartial(lines)
  return [...base, { id: nextId(base), role: "user" as const, text, partial: true }].slice(-MAX_LINES)
}
