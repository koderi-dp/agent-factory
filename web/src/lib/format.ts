/** Durations accept seconds; timestamps are ISO dates. Inject now for deterministic rendering. */
export function transferred(receivedBytes: number, totalBytes: number): string {
  const format = (bytes: number) => `${(bytes / 1_000_000).toFixed(1)} MB`;
  return `${format(receivedBytes)} / ${format(totalBytes)}`;
}

export function duration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const total = Math.max(0, Math.floor(seconds));
  if (total < 60) return `${total}s`;
  if (total < 3600) return `${Math.floor(total / 60)}m ${total % 60}s`;
  return `${Math.floor(total / 3600)}h ${Math.floor((total % 3600) / 60)}m`;
}
export function tokens(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const count = Math.max(0, value);
  if (count < 1000) return `${Math.floor(count)}`;
  return `${new Intl.NumberFormat("en", { maximumFractionDigits: 1 }).format(count / (count < 1_000_000 ? 1000 : 1_000_000))}${count < 1_000_000 ? "k" : "M"}`;
}
export function relativeTime(at: string | null | undefined, now = Date.now()): string {
  if (!at || !Number.isFinite(Date.parse(at))) return "—";
  const seconds = Math.max(0, Math.floor((now - Date.parse(at)) / 1000));
  if (seconds < 5) return "just now";
  return `${duration(seconds)} ago`;
}
export function clockTime(at: string | null | undefined): string {
  if (!at || !Number.isFinite(Date.parse(at))) return "—";
  return new Date(at).toLocaleString("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
