import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { connectLog } from "@/lib/sse";
import { appendLog, type LogEntry } from "./log-lines";

export type LogSource = { attempt: number; stage: string; agent: string; live: boolean };

export function useLogEntries(repo: string, sources: LogSource[]): LogEntry[] {
  // `dropped` counts the head lines the cap discarded, so entry keys stay tied to their line.
  const [buffers, setBuffers] = useState<Record<number, { text: string; dropped: number }>>({});
  const open = useRef(new Map<number, () => void>());
  const latest = useRef(sources);
  latest.current = sources;
  const signature = sources.map((s) => s.attempt).join(",");

  useEffect(() => {
    const wanted = new Set(latest.current.map((s) => s.attempt));
    for (const [id, cleanup] of open.current) {
      if (wanted.has(id)) continue;
      cleanup();
      open.current.delete(id);
      setBuffers((old) => {
        const { [id]: _dropped, ...rest } = old;
        return rest;
      });
    }
    for (const source of latest.current) {
      if (open.current.has(source.attempt)) continue;
      const id = source.attempt;
      const append = (text: string) =>
        setBuffers((old) => {
          const previous = old[id] ?? { text: "", dropped: 0 },
            next = appendLog(previous.text, text),
            total = previous.text.split("\n").length + text.split("\n").length - 1;
          return {
            ...old,
            [id]: { text: next, dropped: previous.dropped + total - next.split("\n").length },
          };
        });
      setBuffers((old) => (id in old ? old : { ...old, [id]: { text: "", dropped: 0 } }));
      if (source.live) {
        open.current.set(
          id,
          connectLog(repo, id, 0, (batch) => append(batch.text)),
        );
      } else {
        let cancelled = false;
        open.current.set(id, () => {
          cancelled = true;
        });
        void (async () => {
          try {
            let offset = 0;
            for (;;) {
              const page = await api.log(repo, id, offset);
              if (cancelled) return;
              if (page.offset <= offset) return;
              offset = page.offset;
              append(page.text);
            }
          } catch {
            // keep what was loaded so far
          }
        })();
      }
    }
  }, [repo, signature]);

  useEffect(() => {
    const map = open.current;
    return () => {
      for (const cleanup of map.values()) cleanup();
      map.clear();
    };
  }, [repo]);

  return useMemo(
    () =>
      sources.flatMap((s) => {
        const buffer = buffers[s.attempt] ?? { text: "", dropped: 0 };
        const lines = buffer.text.split("\n");
        if (lines.at(-1) === "") lines.pop();
        return lines.map((line, i) => ({
          key: `${s.attempt}:${buffer.dropped + i}`,
          stage: s.stage,
          agent: s.agent,
          line,
        }));
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [buffers, signature, sources.map((s) => `${s.stage}/${s.agent}`).join(",")],
  );
}
