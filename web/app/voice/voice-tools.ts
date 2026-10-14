/**
 * The `/voice` client tools. The agent calls them with ids; they fetch the
 * play through the coach's own session and hand the backend-rendered SVG to
 * the per-call `onShow`. SVG never comes from model output (d#1116, req #546). Ids are
 * model output, so they are only ever used as encoded URL path segments by
 * the injected fetchers.
 *
 * Relative imports only, so `node --test web/app/voice/voice-tools.test.ts`
 * runs it without the Next alias resolver.
 */

import { pickVariant, type Hash } from "../../lib/voice-variant.ts"

export type ShownPlay = {
  id: string
  name: string
  /** The saved variant's source call; "drawn by voice" for a fresh draw. */
  call: string
  svg: string
  source: "saved" | "drawn"
}

type SavedPlayLike = {
  id: string
  name: string
  variants: readonly { label: string; source_call: string; svg: string }[]
}

type DrawLike = { svg: string | null; resolved: boolean }

type Log = (...args: unknown[]) => void
// Returns false when a newer call already owns the screen (see latestOnly).
type OnShow = (play: ShownPlay) => unknown
type ToolHandler = (params: Record<string, unknown>, onShow: OnShow) => Promise<string>

const MAX_ID = 64

function statusOf(err: unknown): number | undefined {
  const status = (err as { status?: unknown } | null)?.status
  return typeof status === "number" ? status : undefined
}

/**
 * The tool result the agent hears when a fetch fails. Only a 404 means the
 * play is missing; a 401/403 or a server error must not read as "not found",
 * or the agent tells the coach the play doesn't exist.
 */
function failureResult(tool: string, err: unknown, log: Log): string {
  const status = statusOf(err)
  if (status === 404) return "not found"
  log(`voice ${tool} failed`, status ?? err)
  if (status === 401 || status === 403) {
    return "error: can't reach the playbook, the coach's sign-in has expired"
  }
  return "error: can't reach the playbook right now"
}

function idParam(params: Record<string, unknown>, key: string): string | null {
  const value = params[key]
  return typeof value === "string" && value.length > 0 && value.length <= MAX_ID ? value : null
}

export function makeShowPlayHandler(
  fetchPlay: (id: string) => Promise<SavedPlayLike>,
  log: Log = console.error,
): ToolHandler {
  return async (params, onShow) => {
    const playId = idParam(params, "play_id")
    if (!playId) return "not found"
    const hash: Hash | undefined =
      params.hash === "left" || params.hash === "right" ? params.hash : undefined
    let play: SavedPlayLike
    try {
      play = await fetchPlay(playId)
    } catch (err) {
      return failureResult("show_play", err, log)
    }
    const picked = pickVariant(play.variants, hash)
    if (!picked) return "not found"
    const { variant, hashMissing } = picked
    if (onShow({ id: play.id, name: play.name, call: variant.source_call, svg: variant.svg, source: "saved" }) === false) {
      return SUPERSEDED
    }
    return hashMissing ? `shown, but no ${hash} hash version is saved` : "shown"
  }
}

export function makeShowDrawHandler(
  fetchDraw: (id: string) => Promise<DrawLike>,
  log: Log = console.error,
): ToolHandler {
  return async (params, onShow) => {
    const drawId = idParam(params, "draw_id")
    if (!drawId) return "not found"
    let draw: DrawLike
    try {
      draw = await fetchDraw(drawId)
    } catch (err) {
      return failureResult("show_draw", err, log)
    }
    if (!draw.resolved || !draw.svg) return "not drawn: the call did not resolve to a play"
    if (onShow({ id: drawId, name: "New draw", call: "drawn by voice", svg: draw.svg, source: "drawn" }) === false) {
      return SUPERSEDED
    }
    return "shown"
  }
}

/**
 * One `onShow` per tool call that only lands if no newer call has started
 * since, so a slow fetch can never replace the play a later call put up.
 */
export function latestOnly(apply: (play: ShownPlay) => void): () => OnShow {
  let latest = 0
  return () => {
    const seq = ++latest
    return (play) => {
      if (seq !== latest) return false
      apply(play)
      return true
    }
  }
}

// Told to the agent so it never says a play is on screen when a newer request replaced it.
const SUPERSEDED = "superseded: a newer request is on screen"
