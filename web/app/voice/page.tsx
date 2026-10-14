"use client"

import { ConversationProvider, useConversation, type DisconnectionDetails } from "@elevenlabs/react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { AppShell } from "@/components/app-shell"
import { getTeamMe } from "@/lib/api"
import { getSavedPlay } from "@/lib/plays"
import { createVoiceSession, getVoiceDraw } from "@/lib/voice"

import {
  ARMED_FOR_MS,
  addFinalLine,
  disconnectProblem,
  endTapAction,
  errorProblem,
  micBlocked,
  micState,
  micUnblockMs,
  sessionProblem,
  setPartialLine,
  settleServerChips,
  toolFinished,
  toolStarted,
  type Line,
  type Problem,
  type ToolChip,
} from "./voice-state"
import { latestOnly, makeShowDrawHandler, makeShowPlayHandler, type ShownPlay } from "./voice-tools"
import { MicDock, PlayPanel, ToolStrip, Transcript } from "./voice-ui"

type ToolHandler = (params: Record<string, unknown>) => Promise<string>
type ShowHandler = (params: Record<string, unknown>, onShow: (play: ShownPlay) => unknown) => Promise<string>

// Dev-only stub for the 390 px Playwright check: no live ElevenLabs session in CI.
type DevStub = { status: string; isSpeaking: boolean } | null

function VoiceConsole() {
  const [shown, setShown] = useState<ShownPlay | null>(null)
  const [problem, setProblem] = useState<Problem | null>(null)
  const [starting, setStarting] = useState(false)
  const [lines, setLines] = useState<Line[]>([])
  const [chips, setChips] = useState<ToolChip[]>([])
  const [level, setLevel] = useState(0)
  const [devStub, setDevStub] = useState<DevStub>(null)
  const [budgetWaited, setBudgetWaited] = useState<Problem | null>(null)
  const clientCalls = useRef(0)
  // When the coach last spoke and a tool last answered: a tap right after either only arms the hang-up.
  const lastUserLineAt = useRef<number | null>(null)
  const lastToolEventAt = useRef<number | null>(null)
  const lastAgentSpokeAt = useRef<number | null>(null)
  const [armedUntil, setArmedUntil] = useState<number | null>(null)
  // Sequence guard shared by show_play and show_draw: only the latest call may set the play.
  const nextShow = useMemo(() => latestOnly(setShown), [])

  // Client tools are observed in their own handlers; each call is one strip chip.
  const tracked = useCallback(
    (name: string, handler: ShowHandler): ToolHandler =>
      async (params) => {
        const id = `client-${++clientCalls.current}`
        setChips((s) => toolStarted(s, { id, name, kind: "client" }))
        let ok = false
        try {
          const result = await handler(params, nextShow())
          ok = result.startsWith("shown")
          return result
        } finally {
          // Always finish the chip, so a throw can't leave "Thinking…" pinned.
          setChips((s) => toolFinished(s, id, ok))
          lastToolEventAt.current = Date.now()
        }
      },
    [nextShow],
  )
  const showPlay = useMemo(() => tracked("show_play", makeShowPlayHandler(getSavedPlay)), [tracked])
  const showDraw = useMemo(() => tracked("show_draw", makeShowDrawHandler(getVoiceDraw)), [tracked])

  const callbacks = useMemo(
    () => ({
      // Final transcript lines, both sides.
      onMessage: ({ role, message }: { role: string; message: string }) => {
        if (role === "user") lastUserLineAt.current = Date.now()
        setLines((prev) => addFinalLine(prev, role === "user" ? "user" : "agent", message))
      },
      // The coach's words while they are still talking (only sent when the
      // agent's client events include tentative_user_transcript).
      onIncomingEvent: (event: { type?: string; tentative_user_transcription_event?: { user_transcript?: string } }) => {
        const partial = event.type === "tentative_user_transcript" ? event.tentative_user_transcription_event?.user_transcript : undefined
        if (!partial) return
        lastUserLineAt.current = Date.now()
        setLines((prev) => setPartialLine(prev, partial))
      },
      // Server (webhook) tools: the SDK's agent_tool_request / agent_tool_response events.
      onAgentToolRequest: ({ tool_name, tool_call_id, tool_type }: { tool_name: string; tool_call_id: string; tool_type: string }) => {
        if (tool_type === "client") return // drawn by the client handlers above
        setChips((s) => toolStarted(s, { id: tool_call_id, name: tool_name, kind: "server" }))
      },
      onAgentToolResponse: ({ tool_call_id, is_error }: { tool_call_id: string; is_error: boolean }) => {
        lastToolEventAt.current = Date.now()
        setChips((s) => toolFinished(s, tool_call_id, !is_error))
      },
      // An interrupted turn never gets its tool responses: stop the server chips.
      onInterruption: () => setChips(settleServerChips),
      onDisconnect: (details: DisconnectionDetails) => {
        setProblem(disconnectProblem(details))
        // No tool response arrives after the call ends: don't leave a chip spinning.
        setChips(settleServerChips)
        if (details.reason !== "user") console.warn("voice disconnected", details)
      },
      onError: (message: string, context?: unknown) => {
        setProblem(errorProblem(message))
        console.warn("voice error", message, context)
      },
    }),
    [],
  )

  const conversation = useConversation({ clientTools: { show_play: showPlay, show_draw: showDraw }, ...callbacks })
  const status = devStub?.status ?? conversation.status
  const isSpeaking = devStub ? devStub.isSpeaking : conversation.isSpeaking
  const live = status === "connected" || status === "connecting"
  const listening = status === "connected" && !isSpeaking

  // The agent speaking means its server tools have answered, even if a response event was lost.
  useEffect(() => {
    if (!isSpeaking) return
    setChips(settleServerChips)
    // The coach's last line counts as answered once the agent starts speaking.
    lastAgentSpokeAt.current = Date.now()
  }, [isSpeaking])

  // An armed hang-up lapses after ARMED_FOR_MS, and never outlives the call.
  useEffect(() => {
    if (armedUntil === null) return
    if (!live) {
      setArmedUntil(null)
      return
    }
    const timer = window.setTimeout(() => setArmedUntil(null), Math.max(0, armedUntil - Date.now()))
    return () => window.clearTimeout(timer)
  }, [armedUntil, live])

  // A budget block lifts once Retry-After has passed; a 503 stays until reload.
  useEffect(() => {
    const ms = micUnblockMs(problem)
    if (ms === null) return
    const timer = window.setTimeout(() => setBudgetWaited(problem), ms)
    return () => window.clearTimeout(timer)
  }, [problem])

  // Local mic level while it's the coach's turn: shows their voice is reaching the browser.
  const { getInputVolume } = conversation
  useEffect(() => {
    if (!listening || devStub) return
    const timer = window.setInterval(() => setLevel(getInputVolume()), 120)
    return () => window.clearInterval(timer)
  }, [listening, devStub, getInputVolume])

  const start = useCallback(async () => {
    // A live call already has a session; starting another would mint an orphan.
    if (live) return
    setProblem(null)
    setLines([])
    setChips([])
    lastUserLineAt.current = null
    lastToolEventAt.current = null
    lastAgentSpokeAt.current = null
    setStarting(true)
    try {
      // Ask for the mic first, so a denial costs no session start from the team's budget.
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      stream.getTracks().forEach((t) => t.stop())
      const session = await createVoiceSession()
      if (process.env.NODE_ENV !== "production") devHooks.startSessionCalls++
      conversation.startSession({
        conversationToken: session.conversation_token,
        connectionType: "webrtc",
        // The tool header takes this variable's whole value, so it carries the scheme.
        dynamicVariables: { secret__hlc_voice_token: `Bearer ${session.voice_token}` },
      })
    } catch (err) {
      const p = sessionProblem(err)
      setProblem(p)
      // Budget, not-configured and a blocked mic are expected states with their own copy.
      if (p.kind === "connection") console.error("voice start failed", err)
    } finally {
      setStarting(false)
    }
  }, [conversation, live])

  // Same rule for mouse, touch, keyboard and screen readers: they all reach the button's click.
  const endTap = useCallback(() => {
    const now = Date.now()
    const action = endTapAction({
      now,
      lastUserLineAt: lastUserLineAt.current,
      lastAgentSpokeAt: lastAgentSpokeAt.current,
      lastToolEventAt: lastToolEventAt.current,
      armedUntil,
    })
    if (action === "arm") {
      setArmedUntil(now + ARMED_FOR_MS)
      return
    }
    setArmedUntil(null)
    if (devStub) setDevStub(null)
    else conversation.endSession()
  }, [armedUntil, devStub, conversation])

  // Dev-only seam (stripped from production by the NODE_ENV constant fold):
  // lets Playwright drive the same handlers a live session would.
  useEffect(() => {
    if (process.env.NODE_ENV === "production") return
    const w = window as unknown as { __hlcVoiceDev?: unknown }
    w.__hlcVoiceDev = {
      showPlay,
      showDraw,
      stub: setDevStub,
      event: (name: keyof typeof callbacks, payload: never) => (callbacks[name] as (p: never) => void)(payload),
      get startSessionCalls() {
        return devHooks.startSessionCalls
      },
    }
    return () => {
      delete w.__hlcVoiceDev
    }
  }, [showPlay, showDraw, callbacks])

  const mic = micState({ status, isSpeaking, toolRunning: chips.some((c) => c.state === "running"), starting, problem })
  const heard = [...lines].reverse().find((l) => l.role === "user") ?? null
  const blocked = micBlocked(problem, budgetWaited === problem)

  return (
    <>
      <PlayPanel play={shown} />
      <ToolStrip chips={chips} />
      <Transcript lines={lines} />
      <MicDock
        mic={mic}
        heard={live ? heard : null}
        level={listening ? level : null}
        live={live}
        armed={live && armedUntil !== null}
        disabled={starting || blocked}
        problem={problem}
        onMic={live ? endTap : start}
        onRetry={start}
      />
    </>
  )
}

const devHooks = { startSessionCalls: 0 }

function TeamName() {
  const [name, setName] = useState<string | null>(null)
  useEffect(() => {
    getTeamMe()
      .then((team) => setName(team?.team_name ?? null))
      .catch(() => setName(null))
  }, [])
  return name ? <span className="truncate text-xs text-muted-foreground">{name}</span> : null
}

export default function VoicePage() {
  return (
    <AppShell>
      {/* One viewport-tall column (minus the shell's header and padding): the
          transcript takes the slack and scrolls, so the play stays on screen and
          the mic stays docked at the bottom for thumb reach. */}
      <div className="mx-auto -mt-3 flex h-[calc(100dvh-5.75rem)] w-full max-w-2xl flex-col gap-3">
        <div className="flex shrink-0 items-baseline justify-between gap-3 border-b border-border pb-2.5">
          <h1 className="text-lg font-semibold tracking-tight">Talk to your playbook</h1>
          <TeamName />
        </div>
        <ConversationProvider>
          <VoiceConsole />
        </ConversationProvider>
      </div>
    </AppShell>
  )
}
