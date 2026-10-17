import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { connectLog } from "@/lib/sse";
import { appendLog, type LogEntry } from "./log-lines";

export type LogSource = { attempt: number; stage: string; agent: string; live: boolean };

export function useLogEntries(repo: string, sources: LogSource[]): LogEntry[] {
  const [buffers, setBuffers] = useState<Record<number, string>>({});
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
        setBuffers((old) => ({ ...old, [id]: appendLog(old[id] ?? "", text) }));
      setBuffers((old) => (id in old ? old : { ...old, [id]: "" }));
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
        const lines = (buffers[s.attempt] ?? "").split("\n");
        if (lines.at(-1) === "") lines.pop();
        return lines.map((line, i) => ({
          key: `${s.attempt}:${i}`,
          stage: s.stage,
          agent: s.agent,
          line,
        }));
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [buffers, signature, sources.map((s) => `${s.stage}/${s.agent}`).join(",")],
  );
}
