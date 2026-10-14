"use client"

/**
 * Presentational pieces of `/voice`, in the approved mockup's order: the play
 * (hero), the tool strip, the transcript, and the docked mic. Layout spec:
 * docs/plans/elevenlabs-voice-demo/dispatch/mockups/voice-page.html. No field
 * geometry here — the play is backend SVG only (req #546).
 */

import { Check, Mic, Square, X } from "lucide-react"
import { useEffect, useRef } from "react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

import type { Line, MicState, Problem, ToolChip } from "./voice-state"
import type { ShownPlay } from "./voice-tools"

export function PlayPanel({ play }: { play: ShownPlay | null }) {
  return (
    <section aria-label="Play on screen" className="shrink-0 overflow-hidden rounded-lg border border-border">
      {play ? (
        <>
          {/* Name in full, source call below it: at 390 px both don't fit one line. */}
          <div className="border-b border-border px-3 py-2">
            <h2 className="text-sm font-semibold leading-tight">{play.name}</h2>
            <p className="truncate text-xs italic text-muted-foreground">{play.call}</p>
          </div>
          <div
            data-testid="voice-play-svg"
            role="img"
            aria-label={`${play.name} diagram`}
            className="[&>svg]:mx-auto [&>svg]:block [&>svg]:h-auto [&>svg]:max-h-[45dvh] [&>svg]:w-full"
            // SVG from HLC's backend renderer (GET /api/v1/plays/saved/{id} or
            // GET /api/v1/voice/draws/{id}), never from model output — same
            // pattern as app/plays/page.tsx.
            // eslint-disable-next-line react/no-danger
            dangerouslySetInnerHTML={{ __html: play.svg }}
          />
        </>
      ) : (
        <div className="grid h-56 place-items-center bg-muted px-6 text-center text-sm text-muted-foreground">
          <p>
            Ask for a play, like &ldquo;show our boundary smash&rdquo;, or have one drawn.
          </p>
        </div>
      )}
    </section>
  )
}

export function ToolStrip({ chips }: { chips: ToolChip[] }) {
  if (chips.length === 0) return null
  return (
    <section aria-label="Tool activity" className="shrink-0">
      <h2 className="mb-1 text-xs text-muted-foreground">Tools</h2>
      <ol className="-mx-[var(--gutter)] flex gap-1.5 overflow-x-auto px-[var(--gutter)] pb-0.5 [scrollbar-width:none]">
        {chips.map((chip) => (
          <li
            key={chip.id}
            data-testid="voice-tool-chip"
            data-tool={chip.name}
            data-state={chip.state}
            className={cn(
              "relative inline-flex flex-none items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-semibold",
              chip.state === "running" ? "border-primary bg-primary/5" : "border-border bg-muted",
            )}
          >
            {chip.state === "running" ? (
              <span aria-hidden className="size-2 rounded-full bg-primary motion-safe:animate-pulse" />
            ) : chip.state === "done" ? (
              <Check aria-hidden className="size-3.5 text-primary" strokeWidth={3} />
            ) : (
              <X aria-hidden className="size-3.5 text-destructive" strokeWidth={3} />
            )}
            <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              {chip.kind}
            </span>
            {chip.name}
            <span className="sr-only">
              {chip.state === "running" ? "running" : chip.state === "done" ? "finished" : "failed"}
            </span>
          </li>
        ))}
      </ol>
    </section>
  )
}

export function Transcript({ lines }: { lines: Line[] }) {
  // Scroll inside the transcript only, so the play (the hero) never scrolls away.
  const box = useRef<HTMLOListElement>(null)
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight
  }, [lines])
  // Screen readers hear each final line once; tentative words would re-announce on every update.
  const lastFinal = lines.findLast((l) => !l.partial)
  return (
    <>
      <p aria-live="polite" className="sr-only">
        {lastFinal ? (
          <span key={lastFinal.id}>
            {lastFinal.role === "user" ? "You: " : "Agent: "}
            {lastFinal.text}
          </span>
        ) : null}
      </p>
      <ol
        ref={box}
        aria-label="Transcript"
        className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto overscroll-contain"
      >
        {lines.map((line) => (
          <li
            key={line.id}
            aria-hidden={line.partial || undefined}
            className={cn(
              "relative max-w-[85%] rounded-2xl px-3 py-2 text-[15px] leading-snug",
              line.role === "user"
                ? "self-end rounded-br-sm bg-primary text-primary-foreground"
                : "self-start rounded-bl-sm bg-muted text-foreground",
              line.partial && "italic opacity-80",
            )}
          >
            <span className="sr-only">{line.role === "user" ? "You: " : "Agent: "}</span>
            {line.text}
          </li>
        ))}
      </ol>
    </>
  )
}

/** Five bars from the local mic level — proof the browser hears the coach. */
function LevelMeter({ level }: { level: number }) {
  return (
    <span aria-hidden className="flex h-4 items-end gap-0.5">
      {[0.08, 0.2, 0.35, 0.5, 0.7].map((t, i) => (
        <span
          key={i}
          className={cn("w-1 rounded-sm", level > t ? "bg-primary" : "bg-border")}
          style={{ height: `${40 + i * 15}%` }}
        />
      ))}
    </span>
  )
}

export function MicDock({
  mic,
  heard,
  level,
  live,
  armed,
  disabled,
  problem,
  onMic,
  onRetry,
}: {
  mic: MicState
  /** The coach's latest words (partial while they talk), shown near the mic. */
  heard: Line | null
  level: number | null
  live: boolean
  /** First tap of a guarded hang-up landed; the next tap ends the call. */
  armed: boolean
  disabled: boolean
  problem: Problem | null
  onMic: () => void
  onRetry: () => void
}) {
  return (
    <div className="shrink-0 border-t border-border pb-[env(safe-area-inset-bottom)] pt-3">
      <div className="flex w-full flex-col items-center gap-2">
        {heard ? (
          <p
            data-testid="voice-heard"
            className={cn(
              "max-w-full truncate text-sm",
              heard.partial ? "italic text-muted-foreground" : "text-foreground",
            )}
          >
            <span className="text-muted-foreground">Heard: </span>&ldquo;{heard.text}&rdquo;
          </p>
        ) : null}
        <p
          role="status"
          aria-live="polite"
          data-testid="voice-state"
          className={cn(
            "flex items-center gap-2 text-lg font-bold",
            mic.tone === "error" && "text-destructive",
            mic.tone === "live" && mic.word === "Listening" && "text-primary",
          )}
        >
          {mic.word}
          {level !== null ? <LevelMeter level={level} /> : null}
          {armed ? <span className="sr-only">. Tap again to end call.</span> : null}
        </p>
        <span className="relative grid place-items-center">
          {live ? (
            <span aria-hidden className="absolute -inset-2 rounded-full bg-destructive/15 motion-safe:animate-pulse" />
          ) : null}
          <button
            type="button"
            onClick={onMic}
            disabled={disabled}
            aria-label={live ? "End call" : "Start call"}
            className={cn(
              "relative grid size-[76px] place-items-center rounded-full text-white shadow-[0_4px_14px_hsl(var(--foreground)/0.25)] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed",
              live ? "bg-destructive" : disabled ? "bg-muted-foreground" : "bg-primary hover:bg-primary/90",
            )}
          >
            {live ? <Square aria-hidden className="size-7" fill="currentColor" /> : <Mic aria-hidden className="size-8" />}
          </button>
        </span>
        {/* A call control, not push-to-talk: say what the red button does in words. */}
        {live ? (
          <span aria-hidden data-testid="voice-end-label" className="mt-1 text-sm font-semibold text-foreground">
            {armed ? "Tap again to end call" : "End call"}
          </span>
        ) : null}
        {problem ? (
          <div
            role={problem.kind === "ended" ? "status" : "alert"}
            data-testid="voice-problem"
            className={cn(
              "w-full rounded-lg border p-3 text-sm leading-snug text-foreground",
              problem.kind === "budget" || problem.kind === "ended"
                ? "border-border bg-muted"
                : "border-destructive/40 bg-destructive/5",
            )}
          >
            <p className="font-semibold">{problem.title}</p>
            <p>{problem.body}</p>
            {/* Hidden while live: a retry then would mint a second session. */}
            {problem.retry && !live ? (
              <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
                Try again
              </Button>
            ) : null}
          </div>
        ) : (
          <p className="text-center text-xs text-muted-foreground">
            {live
              ? "Just talk — I'm listening. Tap End call when you're done."
              : mic.word === "Connecting…"
                ? "Getting a secure session from HLC…"
                : "Try “show our boundary smash” or “draw: spread right hash, field mills, boundary smash”."}
          </p>
        )}
      </div>
    </div>
  )
}
