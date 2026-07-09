/**
 * Warm-set computation (pure — unit-tested in isolation).
 *
 * The live wall reports the cameras it wants kept "warm" (server-side producers
 * held open) so paging/patrol/first-open hit an already-warm producer and start
 * in ~0.5s instead of a 2.6–5s cold dial. We warm the CURRENT page plus the NEXT
 * page (the most likely next view under patrol or a manual page flip), deduped.
 */

export function computeWarmIds(
  filtered: { id: string }[],
  page: number,
  cellCount: number,
  totalPages: number,
): string[] {
  if (cellCount <= 0 || filtered.length === 0) return [];
  const slice = (p: number) => filtered.slice(p * cellCount, p * cellCount + cellCount);
  const current = slice(page);
  // Next page only when there IS a distinct one (avoid warming the same page twice
  // on a single-page wall, where (page+1)%1 === page).
  const nextPage = totalPages > 1 ? (page + 1) % totalPages : page;
  const next = nextPage === page ? [] : slice(nextPage);

  const ids: string[] = [];
  const seen = new Set<string>();
  for (const c of current) {
    if (!seen.has(c.id)) {
      seen.add(c.id);
      ids.push(c.id);
    }
  }
  for (const c of next) {
    if (!seen.has(c.id)) {
      seen.add(c.id);
      ids.push(c.id);
    }
  }
  return ids;
}
