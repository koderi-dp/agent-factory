export function appendLog(previous: string, chunk: string) {
  return (previous + chunk)
    .split("\n")
    .slice(-2000)
    .map((line) => line.slice(0, 16000))
    .join("\n");
}
export function renderLogLine(line: string) {
  try {
    const event = JSON.parse(line);
    if (event.item?.text) return String(event.item.text).replaceAll("\n", " ↵ ");
    if (event.item?.command) return `${event.type}: ${event.item.command}`;
    return String(event.type ?? "event");
  } catch {
    return line;
  }
}

export type LogEntry = { key: string; stage: string; agent: string; line: string };
export type LogMatch = { entry: LogEntry; start: number; end: number };

export function logTimestamp(line: string): Date | null {
  try {
    const stamp = (JSON.parse(line) as { timestamp?: unknown } | null)?.timestamp;
    if (typeof stamp !== "string" && typeof stamp !== "number") return null;
    const date = new Date(stamp);
    return Number.isNaN(date.getTime()) ? null : date;
  } catch {
    return null;
  }
}

export function entryText(entry: LogEntry, raw: boolean) {
  return raw ? entry.line : renderLogLine(entry.line);
}

export function filterEntries(entries: LogEntry[], filter: { stage?: string; agent?: string }) {
  return entries.filter(
    (e) =>
      (filter.stage === undefined || e.stage === filter.stage) &&
      (filter.agent === undefined || e.agent === filter.agent),
  );
}

export function findMatches(entries: LogEntry[], query: string, raw: boolean): LogMatch[] {
  if (!query) return [];
  const needle = query.toLowerCase(),
    matches: LogMatch[] = [];
  for (const entry of entries) {
    const text = entryText(entry, raw).toLowerCase();
    for (let at = text.indexOf(needle); at >= 0; at = text.indexOf(needle, at + needle.length)) {
      matches.push({ entry, start: at, end: at + needle.length });
    }
  }
  return matches;
}

export function formatEntries(entries: LogEntry[], raw: boolean) {
  return entries
    .map((e) => {
      const stamp = logTimestamp(e.line);
      return `${stamp ? stamp.toISOString() : ""}\t${e.stage}/${e.agent}\t${entryText(e, raw)}`;
    })
    .join("\n");
}
