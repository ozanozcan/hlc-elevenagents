// Run: node --test web/lib/voice-variant.test.ts (Node strips the types natively).
import assert from "node:assert/strict"
import test from "node:test"

import { pickVariant } from "./voice-variant.ts"

// Shapes copied from the demo team's saved "Spread Boundary Smash Field Mills".
const mills = [
  { label: "SPREAD FIELD MILLS BOUNDARY SMASH", source_call: "spread left hash field mills boundary smash, move Y hash-1" },
  { label: "SPREAD BOUNDARY SMASH FIELD MILLS", source_call: "spread right hash field mills  boundary smash, move W hash-1" },
  { label: "SPREAD BOUNDARY SMASH FIELD MILLS", source_call: "spread smash mills" },
]

test("right hash picks the variant saved on the right hash", () => {
  assert.deepEqual(pickVariant(mills, "right"), { variant: mills[1], hashMissing: false })
})

test("left hash picks the variant saved on the left hash", () => {
  assert.deepEqual(pickVariant(mills, "left"), { variant: mills[0], hashMissing: false })
})

test("no hash asked prefers the variant saved without a hash", () => {
  assert.deepEqual(pickVariant(mills, undefined), { variant: mills[2], hashMissing: false })
})

test("a hash nobody saved falls back to the first variant and says so", () => {
  const leftOnly = [{ label: "L", source_call: "spread left hash smash 88, move y hash-2" }]
  assert.deepEqual(pickVariant(leftOnly, "right"), { variant: leftOnly[0], hashMissing: true })
})

test("'hash' inside a move instruction is not a hash alignment", () => {
  // "move Y hash-1" must not make a no-alignment call look like a hash call.
  // The no-alignment variant sits second, so the fallback-to-first rule cannot mask a miss.
  const variants = [
    { label: "B", source_call: "spread right hash smash" },
    { label: "A", source_call: "spread smash, move Y hash-1" },
  ]
  assert.deepEqual(pickVariant(variants, undefined), { variant: variants[1], hashMissing: false })
  assert.deepEqual(pickVariant(variants, "right"), { variant: variants[0], hashMissing: false })
})

test("an empty variant list picks nothing", () => {
  assert.equal(pickVariant([], "right"), null)
})
