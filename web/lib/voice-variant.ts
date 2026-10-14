/**
 * Which saved variant `show_play` draws. A saved play holds one variant per
 * hash (e.g. "spread left hash …", "spread right hash …", "spread smash mills"),
 * so drawing the first one showed the left hash to a coach who asked for the
 * right. Pure on purpose: `node --test web/lib/voice-variant.test.ts`.
 */

export type Hash = "left" | "right"

type VariantLike = { source_call: string }

// "right hash" is an alignment; a bare "hash" inside "move Y hash-1" is not.
const ALIGNMENT = /\b(left|right)\s+hash\b/

function alignmentOf(sourceCall: string): Hash | null {
  const match = ALIGNMENT.exec(sourceCall.toLowerCase())
  return match ? (match[1] as Hash) : null
}

export function pickVariant<V extends VariantLike>(
  variants: readonly V[],
  hash: Hash | undefined,
): { variant: V; hashMissing: boolean } | null {
  if (variants.length === 0) return null
  const wanted = hash ?? null
  const match = variants.find((v) => alignmentOf(v.source_call) === wanted)
  if (match) return { variant: match, hashMissing: false }
  // Asked for a hash nobody saved: show what exists and say so. No hash asked
  // and every variant has one: any of them is a fair answer.
  return { variant: variants[0], hashMissing: wanted !== null }
}
