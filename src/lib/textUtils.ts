/**
 * Text quality utilities — shared garbled/mojibake detection
 */

/** Returns true if text is garbled/mojibake (unreadable PDF extraction artefacts) */
export function isTextGarbled(text: string): boolean {
  if (!text || text.length < 30) return false
  const sample = text.substring(0, 1200).replace(/\s/g, '')
  if (sample.length === 0) return true
  // Ratio of normal letters+digits to total characters
  const alphaNum = (sample.match(/[a-zA-Z0-9]/g) || []).length
  const ratio = alphaNum / sample.length
  // Obvious garbage: geometric shapes, box-drawing, replacement chars, bullets, middle dots, misc symbols
  const garbage = (sample.match(/[\u25A0-\u25FF\u2500-\u257F\u2580-\u259F\u2190-\u21FF\uFFFD\u0080-\u009F\u2400-\u243F\u2022\u25CF\u25CB\u25C6\u25C7\u25AA\u25AB\u2B22\u2B24\u00B7\u2023\u2043\u204C\u204D\u2219\u22C5\u2981\u00A4\u00A6\u00AC\u00AD\u2020-\u2027\u2030-\u205E\u25E6\u2981]/g) || []).length
  // Pattern: single letters separated by non-alphanumeric (e.g., •••S•••U•••B) — strong garble indicator
  const isolatedLetters = (sample.match(/[^a-zA-Z0-9][a-zA-Z][^a-zA-Z0-9]/g) || []).length
  const isolatedRatio = sample.length > 0 ? isolatedLetters / sample.length : 0
  // Runs of 3+ identical non-alphanum characters (e.g., ••••, +++, ---)
  const repeatedRuns = (sample.match(/([^a-zA-Z0-9\s])\1{2,}/g) || []).length
  // Mojibake markers (UTF-8 misinterpreted as Latin-1)
  const mojibakeMarkers = [
    '\u00C3\u0082', '\u00C3\u0083', '\u00C3\u00A9', '\u00C3\u00A8',
    '\u00C3\u00BC', '\u00C3\u00B6', '\u00C2\u00A0', '\u00C2\u00AE',
    'Ã\u0082', 'Ã\u0083', 'Ã©', 'Ã¨', 'Ã¼', 'Ã¶', 'Â®', 'Â«', 'Â»', 'Â\u00A0',
  ]
  let mojibakeHits = 0
  for (const m of mojibakeMarkers) {
    let pos = 0
    while ((pos = text.indexOf(m, pos)) !== -1) { mojibakeHits++; pos += m.length }
  }
  // Garbled if: <40% readable, OR >10 garbage chars, OR many isolated single letters,
  // OR many repeated symbol runs, OR multiple mojibake markers found
  return ratio < 0.4 || garbage > 10 || isolatedRatio > 0.06 || repeatedRuns > 5 || mojibakeHits >= 3
}
