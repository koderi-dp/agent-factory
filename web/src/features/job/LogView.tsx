import { appendLog, renderLogLine } from "./log-lines";
import { useEffect, useState } from "react";
import { connectLog } from "@/lib/sse";
import { Button } from "@/ui/primitives/button";

export function LogView({ repo, attempt }: { repo: string; attempt?: number }) {
  const [text, setText] = useState("");
  const [raw, setRaw] = useState(false),
    [scroll, setScroll] = useState(0);
  useEffect(() => {
    if (attempt == null) return;
    return connectLog(repo, attempt, 0, (batch) => setText((old) => appendLog(old, batch.text)));
  }, [repo, attempt]);
  const lines = text.split("\n"),
    rowHeight = 24,
    viewport = 384;
  const first = Math.max(0, Math.floor(scroll / rowHeight) - 5),
    last = Math.min(lines.length, first + 26);
  if (attempt == null) return <p className="text-sm text-muted-foreground">No attempt yet.</p>;
  return (
    <section aria-label="Attempt log" className="space-y-2">
      <Button variant="outline" onClick={() => setRaw(!raw)} aria-pressed={raw}>
        Raw {raw ? "on" : "off"}
      </Button>
      <div
        className="overflow-auto rounded bg-inset font-mono text-xs"
        style={{ height: viewport }}
        tabIndex={0}
        onScroll={(e) => setScroll(e.currentTarget.scrollTop)}
      >
        <div style={{ height: lines.length * rowHeight, position: "relative" }}>
          {lines.slice(first, last).map((line, i) => (
            <div
              key={first + i}
              style={{
                position: "absolute",
                top: (first + i) * rowHeight,
                height: rowHeight,
                minWidth: "100%",
                whiteSpace: "pre",
              }}
            >
              {raw ? line : renderLogLine(line)}
            </div>
          ))}
        </div>
      </div>
      <p className="text-xs text-muted-foreground">Last {lines.length} lines · scroll to inspect</p>
    </section>
  );
}
