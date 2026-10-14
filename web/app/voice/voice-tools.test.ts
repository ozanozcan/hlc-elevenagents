// Run: node --test web/app/voice/voice-tools.test.ts (Node strips the types natively).
import assert from "node:assert/strict"
import test from "node:test"

import { latestOnly, makeShowDrawHandler, makeShowPlayHandler, type ShownPlay } from "./voice-tools.ts"

const STUB_SVG = '<svg data-stub="draw"></svg>'

function httpError(status: number): Error & { status: number } {
  return Object.assign(new Error(`status ${status}`), { status })
}

function recorder() {
  const shown: ShownPlay[] = []
  const logged: unknown[][] = []
  return { shown, logged, onShow: (p: ShownPlay) => shown.push(p), log: (...a: unknown[]) => logged.push(a) }
}

test("show_draw renders the drawn SVG and answers 'shown'", async () => {
  const r = recorder()
  const seen: string[] = []
  const handler = makeShowDrawHandler(async (id) => {
      seen.push(id)
      return { svg: STUB_SVG, resolved: true }
    },
    r.log,
  )
  assert.equal(await handler({ draw_id: "abc" }, r.onShow), "shown")
  assert.deepEqual(seen, ["abc"])
  assert.equal(r.shown.length, 1)
  assert.equal(r.shown[0].svg, STUB_SVG)
  assert.equal(r.shown[0].source, "drawn")
})

test("show_draw on a 404 answers 'not found' and keeps the previous play", async () => {
  const r = recorder()
  const handler = makeShowDrawHandler(async () => Promise.reject(httpError(404)), r.log)
  assert.equal(await handler({ draw_id: "gone" }, r.onShow), "not found")
  assert.equal(r.shown.length, 0)
  assert.equal(r.logged.length, 0)
})

test("show_draw with an unresolved draw shows nothing and says so", async () => {
  const r = recorder()
  const handler = makeShowDrawHandler(async () => ({ svg: null, resolved: false }), r.log)
  assert.match(await handler({ draw_id: "abc" }, r.onShow), /not drawn/)
  assert.equal(r.shown.length, 0)
})

test("show_draw rejects a missing or oversized draw_id without fetching", async () => {
  const r = recorder()
  let calls = 0
  const handler = makeShowDrawHandler(async () => {
    calls++
    return { svg: STUB_SVG, resolved: true }
  }, r.log)
  assert.equal(await handler({}, r.onShow), "not found")
  assert.equal(await handler({ draw_id: "x".repeat(200) }, r.onShow), "not found")
  assert.equal(calls, 0)
})

const play = {
  id: "p1",
  name: "Spread Boundary Smash Field Mills",
  variants: [{ label: "SPREAD SMASH", source_call: "spread smash mills", svg: STUB_SVG }],
}

test("show_play on a 404 answers 'not found' without logging", async () => {
  const r = recorder()
  const handler = makeShowPlayHandler(async () => Promise.reject(httpError(404)), r.log)
  assert.equal(await handler({ play_id: "p1" }, r.onShow), "not found")
  assert.equal(r.logged.length, 0)
})

for (const status of [401, 403]) {
  test(`show_play on a ${status} reports a sign-in problem, not a missing play`, async () => {
    const r = recorder()
    const handler = makeShowPlayHandler(async () => Promise.reject(httpError(status)), r.log)
    const result = await handler({ play_id: "p1" }, r.onShow)
    assert.notEqual(result, "not found")
    assert.match(result, /sign/i)
    assert.equal(r.logged.length, 1)
  })
}

test("show_play on a 5xx reports the playbook unreachable and logs it", async () => {
  const r = recorder()
  const handler = makeShowPlayHandler(async () => Promise.reject(httpError(502)), r.log)
  const result = await handler({ play_id: "p1" }, r.onShow)
  assert.notEqual(result, "not found")
  assert.match(result, /can't reach the playbook/)
  assert.equal(r.logged.length, 1)
})

test("show_draw on a 5xx reports the playbook unreachable and logs it", async () => {
  const r = recorder()
  const handler = makeShowDrawHandler(async () => Promise.reject(httpError(500)), r.log)
  assert.match(await handler({ draw_id: "abc" }, r.onShow), /can't reach the playbook/)
  assert.equal(r.logged.length, 1)
})

test("show_play still picks the variant for the asked hash and shows it", async () => {
  const r = recorder()
  const twoHash = {
    ...play,
    variants: [
      { label: "L", source_call: "spread left hash smash", svg: "<svg>L</svg>" },
      { label: "R", source_call: "spread right hash smash", svg: "<svg>R</svg>" },
    ],
  }
  const handler = makeShowPlayHandler(async () => twoHash, r.log)
  assert.equal(await handler({ play_id: "p1", hash: "right" }, r.onShow), "shown")
  assert.equal(r.shown[0].svg, "<svg>R</svg>")
  assert.equal(r.shown[0].source, "saved")
})

test("a slow show_play resolving after a newer call does not replace the newer play", async () => {
  const shown: ShownPlay[] = []
  const nextShow = latestOnly((p) => shown.push(p))
  let releaseSlow: (p: typeof play) => void = () => {}
  const slow = new Promise<typeof play>((resolve) => (releaseSlow = resolve))
  const handler = makeShowPlayHandler(async (id) => (id === "slow" ? slow : { ...play, id: "fast" }), () => {})
  const first = handler({ play_id: "slow" }, nextShow())
  assert.equal(await handler({ play_id: "fast" }, nextShow()), "shown")
  releaseSlow({ ...play, id: "slow" })
  // The agent must not be told a play is on screen when it never landed.
  assert.match(await first, /^superseded/)
  assert.deepEqual(shown.map((p) => p.id), ["fast"])
})

test("a slow show_play does not replace a newer show_draw either", async () => {
  const shown: ShownPlay[] = []
  const nextShow = latestOnly((p) => shown.push(p))
  let releaseSlow: (p: typeof play) => void = () => {}
  const slow = new Promise<typeof play>((resolve) => (releaseSlow = resolve))
  const showPlay = makeShowPlayHandler(async () => slow, () => {})
  const showDraw = makeShowDrawHandler(async () => ({ svg: STUB_SVG, resolved: true }), () => {})
  const first = showPlay({ play_id: "p1" }, nextShow())
  await showDraw({ draw_id: "abc" }, nextShow())
  releaseSlow(play)
  assert.match(await first, /^superseded/)
  assert.deepEqual(shown.map((p) => p.source), ["drawn"])
})

test("calls that resolve in order each show their play", async () => {
  const shown: ShownPlay[] = []
  const nextShow = latestOnly((p) => shown.push(p))
  const handler = makeShowPlayHandler(async (id) => ({ ...play, id }), () => {})
  await handler({ play_id: "a" }, nextShow())
  await handler({ play_id: "b" }, nextShow())
  assert.deepEqual(shown.map((p) => p.id), ["a", "b"])
})
