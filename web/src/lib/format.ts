// TODO(Session 3.2, plan §13.3): implement the shared duration, usage, and relative-time formatters.
export function transferred(receivedBytes: number, totalBytes: number): string {
  const format = (bytes: number) => `${(bytes / 1_000_000).toFixed(1)} MB`;
  return `${format(receivedBytes)} / ${format(totalBytes)}`;
}
