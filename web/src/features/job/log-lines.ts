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
