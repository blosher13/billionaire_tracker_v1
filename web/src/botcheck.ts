/**
 * The bot check's proof of work: find a number n where SHA-256("<challenge>:<n>") starts with
 * `bits` zero bits. About a second for one post; costly for a bot posting thousands.
 */
export async function solveChallenge(challenge: string, bits: number): Promise<number> {
  const encoder = new TextEncoder()
  const BATCH = 1000
  for (let start = 0; ; start += BATCH) {
    const digests = await Promise.all(
      Array.from({ length: BATCH }, (_, i) => crypto.subtle.digest('SHA-256', encoder.encode(`${challenge}:${start + i}`))),
    )
    const found = digests.findIndex((d) => leadingZeroBits(new Uint8Array(d)) >= bits)
    if (found >= 0) return start + found
  }
}

function leadingZeroBits(bytes: Uint8Array): number {
  let bits = 0
  for (const byte of bytes) {
    if (byte !== 0) return bits + Math.clz32(byte) - 24
    bits += 8
  }
  return bits
}
