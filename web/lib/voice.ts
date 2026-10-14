/**
 * Voice session + draw fetchers for `/voice`.
 *
 * The ElevenLabs API key never reaches the browser: `POST /api/v1/voice/session`
 * returns a WebRTC conversation token plus an HLC voice token, and the voice
 * token is handed to the agent as a `secret__` dynamic variable (only ever
 * placed in tool headers, never shown to the LLM). The client-tool handlers
 * live in `app/voice/voice-tools.ts`.
 */

import { z } from "zod"

import { ApiError } from "@/lib/plays"

const voiceSessionSchema = z.object({
  conversation_token: z.string().min(1),
  voice_token: z.string().min(1),
  conversation_id: z.string().nullable(),
  expires_in: z.number(),
})

export type VoiceSession = z.infer<typeof voiceSessionSchema>

/** A failed voice request; `retryAfterSeconds` comes from a 429's `Retry-After`. */
export class VoiceApiError extends ApiError {
  retryAfterSeconds: number | null
  constructor(status: number, message: string, retryAfterSeconds: number | null = null) {
    super(status, message)
    this.name = "VoiceApiError"
    this.retryAfterSeconds = retryAfterSeconds
  }
}

async function voiceError(res: Response): Promise<VoiceApiError> {
  let detail = res.statusText || `Request failed with status ${res.status}`
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (typeof body.detail === "string") detail = body.detail
  } catch {
    // non-JSON error body — keep the status text
  }
  const retryAfter = Number(res.headers.get("Retry-After"))
  return new VoiceApiError(res.status, detail, Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null)
}

/** POST /api/v1/voice/session (same-origin, through the Next rewrite). */
export async function createVoiceSession(): Promise<VoiceSession> {
  const res = await fetch("/api/v1/voice/session", { method: "POST" })
  if (!res.ok) throw await voiceError(res)
  return voiceSessionSchema.parse(await res.json())
}

// The PlayDrawResponse fields `/voice` reads (lane B serves the full shape).
const voiceDrawSchema = z.object({
  resolved: z.boolean(),
  svg: z.string().nullable(),
  clarification_notes: z.array(z.string()).optional().default([]),
  coach_attention: z
    .array(z.object({ kind: z.string(), position_code: z.string(), message: z.string() }))
    .optional()
    .default([]),
})

export type VoiceDraw = z.infer<typeof voiceDrawSchema>

/** GET /api/v1/voice/draws/:id — a draw_play result stashed for this coach's team. */
export async function getVoiceDraw(drawId: string): Promise<VoiceDraw> {
  const res = await fetch(`/api/v1/voice/draws/${encodeURIComponent(drawId)}`)
  if (!res.ok) throw await voiceError(res)
  return voiceDrawSchema.parse(await res.json())
}
