// Run: node --test web/app/voice/voice-state.test.ts (Node strips the types natively).
import assert from "node:assert/strict"
import test from "node:test"

import {
  addFinalLine,
  ARM_WINDOW_MS,
  ARMED_FOR_MS,
  disconnectProblem,
  endTapAction,
  micBlocked,
  micState,
  micUnblockMs,
  sessionProblem,
  setPartialLine,
  settleServerChips,
  toolFinished,
  toolStarted,
  type ToolChip,
} from "./voice-state.ts"

const err = (status: number, extra: object = {}) => Object.assign(new Error("x"), { status, ...extra })

test("a 429 with Retry-After 600 says try again in 10 min, no retry button", () => {
  const p = sessionProblem(err(429, { retryAfterSeconds: 600 }))
  assert.equal(p.kind, "budget")
  assert.match(p.title, /used its voice minutes/)
  assert.match(p.body, /try again in 10 min/i)
  assert.equal(p.retry, false)
})

test("a 429 rounds a partial minute up and never says 0 min", () => {
  assert.match(sessionProblem(err(429, { retryAfterSeconds: 61 })).body, /2 min/)
  assert.match(sessionProblem(err(429, { retryAfterSeconds: 5 })).body, /1 min/)
})

test("a 429 without Retry-After still reads as the budget, without a number", () => {
  const p = sessionProblem(err(429))
  assert.equal(p.kind, "budget")
  assert.doesNotMatch(p.body, /\d/)
})

test("a 503 says voice isn't set up", () => {
  const p = sessionProblem(err(503))
  assert.equal(p.kind, "unconfigured")
  assert.equal(p.title, "Voice isn't set up on this server.")
  assert.equal(p.retry, false)
})

test("a denied microphone asks to allow it and offers a retry", () => {
  const denied = Object.assign(new Error("Permission denied"), { name: "NotAllowedError" })
  const p = sessionProblem(denied)
  assert.equal(p.kind, "mic")
  assert.equal(p.state, "Microphone blocked")
  assert.match(p.body, /allow microphone/i)
  assert.equal(p.retry, true)
})

test("any other start failure shows its reason with a retry", () => {
  const p = sessionProblem(new Error("network down"))
  assert.equal(p.kind, "connection")
  assert.match(p.body, /network down/)
  assert.equal(p.retry, true)
})

test("an error disconnect keeps the SDK reason and close code", () => {
  const p = disconnectProblem({ reason: "error", message: "ICE failed", closeCode: 1006, closeReason: "abnormal" })
  assert.ok(p)
  assert.match(p.body, /ICE failed/)
  assert.match(p.body, /1006: abnormal/)
  assert.equal(p.retry, true)
})

test("the coach ending the call is not a problem", () => {
  assert.equal(disconnectProblem({ reason: "user" }), null)
})

test("the agent ending the call is shown, but not as an error", () => {
  const p = disconnectProblem({ reason: "agent" })
  assert.ok(p)
  assert.equal(p.kind, "ended")
})

test("a tool chip runs, then finishes in place", () => {
  let strip: ToolChip[] = []
  strip = toolStarted(strip, { id: "1", name: "search_plays", kind: "server" })
  assert.deepEqual(strip, [{ id: "1", name: "search_plays", kind: "server", state: "running" }])
  strip = toolStarted(strip, { id: "2", name: "show_play", kind: "client" })
  strip = toolFinished(strip, "1", true)
  strip = toolFinished(strip, "2", false)
  assert.deepEqual(
    strip.map((c) => [c.name, c.state]),
    [
      ["search_plays", "done"],
      ["show_play", "error"],
    ],
  )
})

test("a repeated request id does not add a second chip", () => {
  let strip: ToolChip[] = toolStarted([], { id: "1", name: "draw_play", kind: "server" })
  strip = toolStarted(strip, { id: "1", name: "draw_play", kind: "server" })
  assert.equal(strip.length, 1)
})

test("the strip keeps only the latest chips", () => {
  let strip: ToolChip[] = []
  for (let i = 0; i < 20; i++) strip = toolStarted(strip, { id: String(i), name: `t${i}`, kind: "server" })
  assert.ok(strip.length <= 8)
  assert.equal(strip.at(-1)?.name, "t19")
})

test("a partial user line updates in place, then the final line replaces it", () => {
  let lines = addFinalLine([], "agent", "Hi coach.")
  lines = setPartialLine(lines, "show our")
  lines = setPartialLine(lines, "show our boundary sm")
  const partialId = lines.at(-1)?.id
  assert.deepEqual(lines.at(-1), { id: partialId, role: "user", text: "show our boundary sm", partial: true })
  assert.equal(lines.length, 2)
  lines = addFinalLine(lines, "user", "Show our boundary smash.")
  assert.deepEqual(lines.at(-1), { id: partialId, role: "user", text: "Show our boundary smash.", partial: false })
  assert.equal(lines.length, 2)
})

test("blank transcript text is ignored", () => {
  assert.deepEqual(addFinalLine([], "agent", "   "), [])
  assert.deepEqual(setPartialLine([], " "), [])
})

test("the state word follows the call", () => {
  const base = { status: "disconnected", isSpeaking: false, toolRunning: false, starting: false, problem: null }
  assert.equal(micState(base).word, "Start call")
  assert.equal(micState({ ...base, starting: true }).word, "Connecting…")
  assert.equal(micState({ ...base, status: "connecting" }).word, "Connecting…")
  assert.equal(micState({ ...base, status: "connected" }).word, "Listening")
  assert.equal(micState({ ...base, status: "connected", isSpeaking: true }).word, "Speaking")
  assert.equal(micState({ ...base, status: "connected", toolRunning: true }).word, "Thinking…")
  const problem = sessionProblem(err(503))
  assert.equal(micState({ ...base, problem }).word, problem.state)
  assert.equal(micState({ ...base, problem }).tone, "error")
  assert.equal(micState({ ...base, problem: disconnectProblem({ reason: "agent" }) }).word, "Call ended")
})

test("transcript line ids keep rising, past the line cap too", () => {
  let lines = addFinalLine([], "agent", "line 0")
  const ids = [lines[0].id]
  for (let i = 1; i < 50; i++) {
    lines = addFinalLine(lines, i % 2 ? "user" : "agent", `line ${i}`)
    ids.push(lines.at(-1)!.id)
  }
  for (let i = 1; i < ids.length; i++) assert.ok(ids[i] > ids[i - 1], `id ${ids[i]} after ${ids[i - 1]}`)
  assert.equal(new Set(lines.map((l) => l.id)).size, lines.length)
})

test("a speaking agent reads Speaking even with a tool chip stuck running", () => {
  const live = { status: "connected", isSpeaking: true, toolRunning: true, starting: false, problem: null }
  assert.equal(micState(live).word, "Speaking")
  assert.equal(micState({ ...live, isSpeaking: false }).word, "Thinking…")
})

test("settling marks running server chips done and leaves client chips alone", () => {
  let strip: ToolChip[] = toolStarted([], { id: "s1", name: "search_plays", kind: "server" })
  strip = toolStarted(strip, { id: "c1", name: "show_play", kind: "client" })
  strip = toolStarted(strip, { id: "s2", name: "draw_play", kind: "server" })
  strip = toolFinished(strip, "s2", false)
  assert.deepEqual(
    settleServerChips(strip).map((c) => [c.id, c.state]),
    [
      ["s1", "done"],
      ["c1", "running"],
      ["s2", "error"],
    ],
  )
})

test("the budget lock lifts after Retry-After; a 503 never lifts on its own", () => {
  const budget = sessionProblem(err(429, { retryAfterSeconds: 600 }))
  assert.equal(micUnblockMs(budget), 600_000)
  assert.equal(micBlocked(budget, false), true)
  assert.equal(micBlocked(budget, true), false)
  const unconfigured = sessionProblem(err(503))
  assert.equal(micUnblockMs(unconfigured), null)
  // A monthly cap sends up to ~31 days; setTimeout fires at once past 2^31-1 ms.
  const month = micUnblockMs(sessionProblem(err(429, { retryAfterSeconds: 31 * 86_400 })))
  assert.ok(month !== null && month > 0 && month <= 2_147_483_647, `month delay ${month}`)
  assert.equal(micBlocked(unconfigured, true), true)
  assert.equal(micBlocked(sessionProblem(new Error("x")), false), false)
  assert.equal(micBlocked(null, false), false)
})

test("the arm window is 1.5 s and an armed tap lasts 3 s", () => {
  assert.equal(ARM_WINDOW_MS, 1500)
  assert.equal(ARMED_FOR_MS, 3000)
})

test("a tap with nothing recent ends the call", () => {
  assert.equal(endTapAction({ now: 10_000, lastUserLineAt: null, lastAgentSpokeAt: null, lastToolEventAt: null, armedUntil: null }), "end")
})

test("a tap within 1.5 s of the coach's last line only arms", () => {
  // The agent answered at once, so only the fallback window applies.
  const t = { lastToolEventAt: null, armedUntil: null, lastAgentSpokeAt: 10_000 }
  assert.equal(endTapAction({ ...t, now: 10_000, lastUserLineAt: 10_000 }), "arm")
  assert.equal(endTapAction({ ...t, now: 11_499, lastUserLineAt: 10_000 }), "arm")
  assert.equal(endTapAction({ ...t, now: 11_500, lastUserLineAt: 10_000 }), "end")
})

test("a tap within 1.5 s of a finished tool call only arms", () => {
  const t = { lastUserLineAt: null, lastAgentSpokeAt: null, armedUntil: null }
  assert.equal(endTapAction({ ...t, now: 20_200, lastToolEventAt: 20_000 }), "arm")
  assert.equal(endTapAction({ ...t, now: 21_500, lastToolEventAt: 20_000 }), "end")
  // Old user line, fresh tool event: the fresh one still arms.
  assert.equal(endTapAction({ now: 20_200, lastUserLineAt: 1_000, lastAgentSpokeAt: 1_500, lastToolEventAt: 20_000, armedUntil: null }), "arm")
})

test("a second tap while armed ends the call, even inside the window", () => {
  const recent = { now: 10_500, lastUserLineAt: 10_000, lastAgentSpokeAt: null, lastToolEventAt: 10_000 }
  assert.equal(endTapAction({ ...recent, armedUntil: 13_000 }), "end")
})

test("an expired arm does not count: a tap in a fresh window arms again", () => {
  assert.equal(endTapAction({ now: 20_000, lastUserLineAt: 19_800, lastAgentSpokeAt: 19_900, lastToolEventAt: null, armedUntil: 13_000 }), "arm")
  assert.equal(endTapAction({ now: 13_000, lastUserLineAt: 12_900, lastAgentSpokeAt: 12_950, lastToolEventAt: null, armedUntil: 13_000 }), "arm")
})

test("a tap while the coach's last line is still unanswered only arms, however long the agent thinks", () => {
  // Rehearsal case: the coach spoke at 0, the agent is still thinking at 2.5 s, the coach taps.
  const t = { lastToolEventAt: null, armedUntil: null, lastUserLineAt: 0 }
  assert.equal(endTapAction({ ...t, now: 2_500, lastAgentSpokeAt: null }), "arm")
  assert.equal(endTapAction({ ...t, now: 2_500, lastAgentSpokeAt: -5_000 }), "arm") // spoke before the line
  // Once the agent has answered and the window has passed, one tap ends the call.
  assert.equal(endTapAction({ ...t, now: 2_500, lastAgentSpokeAt: 1_800 }), "end")
})
