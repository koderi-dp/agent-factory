import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { toast } from "sonner";
import { Button } from "@/ui/primitives/button";
import { Input } from "@/ui/primitives/input";
import { Select } from "@/ui/primitives/select";
import {
  entryText,
  filterEntries,
  findMatches,
  formatEntries,
  logTimestamp,
  type LogMatch,
} from "./log-lines";
import { useLogEntries, type LogSource } from "./use-log-entries";

const rowHeight = 24,
  viewport = 384;

function clock(line: string) {
  const date = logTimestamp(line);
  return date
    ? [date.getHours(), date.getMinutes(), date.getSeconds()]
        .map((n) => String(n).padStart(2, "0"))
        .join(":")
    : "";
}

function Highlighted({
  text,
  matches,
  current,
}: {
  text: string;
  matches: LogMatch[];
  current?: LogMatch;
}) {
  if (!matches.length) return <>{text}</>;
  const parts: ReactNode[] = [];
  let at = 0;
  for (const m of matches) {
    parts.push(text.slice(at, m.start));
    const isCurrent = m === current;
    parts.push(
      <mark
        key={m.start}
        className={
          isCurrent ? "bg-primary text-primary-foreground" : "bg-yellow-300/40 text-inherit"
        }
        aria-current={isCurrent ? "true" : undefined}
      >
        {text.slice(m.start, m.end)}
      </mark>,
    );
    at = m.end;
  }
  parts.push(text.slice(at));
  return <>{parts}</>;
}

export function LogView({
  repo,
  sources,
  fileName,
}: {
  repo: string;
  sources: LogSource[];
  fileName?: string;
}) {
  const all = useLogEntries(repo, sources);
  const [raw, setRaw] = useState(false),
    [scroll, setScroll] = useState(0),
    [stage, setStage] = useState<string>(),
    [agent, setAgent] = useState<string>(),
    [query, setQuery] = useState(""),
    [following, setFollowing] = useState(true),
    [currentKey, setCurrentKey] = useState<{ key: string; start: number }>();
  const box = useRef<HTMLDivElement>(null),
    programmatic = useRef(false);

  const stages = [...new Set(sources.map((s) => s.stage))];
  const agents = [
    ...new Set(sources.filter((s) => !stage || s.stage === stage).map((s) => s.agent)),
  ];
  const showStage = stages.length > 1,
    showAgent = new Set(sources.map((s) => s.agent)).size > 1;
  const multi = sources.length > 1;
  const entries = useMemo(() => filterEntries(all, { stage, agent }), [all, stage, agent]);
  const matches = useMemo(() => findMatches(entries, query, raw), [entries, query, raw]);
  const matchesByEntry = useMemo(() => {
    const map = new Map<string, LogMatch[]>();
    for (const m of matches) map.set(m.entry.key, [...(map.get(m.entry.key) ?? []), m]);
    return map;
  }, [matches]);
  const found = currentKey
    ? matches.findIndex((m) => m.entry.key === currentKey.key && m.start === currentKey.start)
    : -1;
  const index = found >= 0 ? found : matches.length ? 0 : -1;
  const current = index >= 0 ? matches[index] : undefined;
  const rowOf = useMemo(() => new Map(entries.map((e, i) => [e.key, i])), [entries]);

  const scrollTo = useCallback((top: number) => {
    const el = box.current;
    if (!el) return;
    const before = el.scrollTop;
    el.scrollTop = top;
    // A scroll event only fires when the position moves; flagging a no-op would swallow the
    // next user scroll.
    if (el.scrollTop !== before) programmatic.current = true;
    setScroll(el.scrollTop);
  }, []);

  useLayoutEffect(() => {
    if (following) scrollTo(entries.length * rowHeight);
  }, [entries, following, scrollTo]);

  const goTo = (target: number) => {
    if (!matches.length) return;
    const next = (target + matches.length) % matches.length,
      m = matches[next];
    setCurrentKey({ key: m.entry.key, start: m.start });
    setFollowing(false);
    scrollTo((rowOf.get(m.entry.key) ?? 0) * rowHeight - viewport / 2 + rowHeight / 2);
  };

  // Pin the current match to its entry, so newly streamed matches do not take its place.
  useEffect(() => {
    if (current && (currentKey?.key !== current.entry.key || currentKey.start !== current.start))
      setCurrentKey({ key: current.entry.key, start: current.start });
  }, [current, currentKey]);

  useEffect(() => {
    setAgent((a) => (a && agents.includes(a) ? a : undefined));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage]);

  const text = () => formatEntries(entries, raw);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text());
      toast.success(`Copied ${entries.length} entries`);
    } catch {
      toast.error("Could not copy the log");
    }
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([text()], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = fileName ? `${fileName}-log.txt` : "log.txt";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  if (!sources.length) return <p className="text-sm text-muted-foreground">No attempt yet.</p>;
  const first = Math.max(0, Math.floor(scroll / rowHeight) - 5),
    last = Math.min(entries.length, first + 26);
  const visible = entries.slice(first, last);
  return (
    <section aria-label="Attempt log" className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {showStage && (
          <Select
            aria-label="Stage"
            fieldSize="sm"
            className="w-40"
            value={stage ?? ""}
            onChange={(e) => setStage(e.target.value || undefined)}
          >
            <option value="">All stages</option>
            {stages.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        )}
        {showAgent && (
          <Select
            aria-label="Agent"
            fieldSize="sm"
            className="w-40"
            value={agent ?? ""}
            onChange={(e) => setAgent(e.target.value || undefined)}
          >
            <option value="">All agents</option>
            {agents.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </Select>
        )}
        <Input
          aria-label="Search log"
          placeholder="Search log"
          className="h-8 w-56"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setCurrentKey(undefined);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              goTo(e.shiftKey ? index - 1 : index + 1);
            } else if (e.key === "Escape") {
              setQuery("");
              setCurrentKey(undefined);
            }
          }}
        />
        <span aria-live="polite" className="text-xs text-muted-foreground">
          {query ? (matches.length ? `${index + 1} of ${matches.length}` : "No matches") : ""}
        </span>
        <Button variant="outline" onClick={() => goTo(index - 1)} disabled={!matches.length}>
          Previous
        </Button>
        <Button variant="outline" onClick={() => goTo(index + 1)} disabled={!matches.length}>
          Next
        </Button>
        <Button variant="outline" onClick={() => setRaw(!raw)} aria-pressed={raw}>
          Raw {raw ? "on" : "off"}
        </Button>
        <Button variant="outline" onClick={() => void copy()}>
          Copy
        </Button>
        <Button variant="outline" onClick={download}>
          Download
        </Button>
      </div>
      <div
        ref={box}
        className="overflow-auto rounded bg-inset font-mono text-xs"
        style={{ height: viewport }}
        tabIndex={0}
        onScroll={(e) => {
          const el = e.currentTarget;
          setScroll(el.scrollTop);
          if (programmatic.current) {
            programmatic.current = false;
            return;
          }
          if (el.scrollHeight - el.clientHeight - el.scrollTop > rowHeight) setFollowing(false);
        }}
      >
        <div style={{ height: entries.length * rowHeight, position: "relative" }}>
          {visible.map((entry, i) => (
            <div
              key={entry.key}
              style={{
                position: "absolute",
                top: (first + i) * rowHeight,
                height: rowHeight,
                minWidth: "100%",
                whiteSpace: "pre",
              }}
            >
              <span className="inline-block w-[8ch] text-muted-foreground">
                {clock(entry.line)}
              </span>{" "}
              {multi && (
                <span className="text-muted-foreground">
                  {entry.stage}/{entry.agent}{" "}
                </span>
              )}
              <Highlighted
                text={entryText(entry, raw)}
                matches={matchesByEntry.get(entry.key) ?? []}
                current={current}
              />
            </div>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <p>{following ? `${entries.length} entries · following` : "Auto-scroll paused"}</p>
        {!following && (
          <Button
            variant="outline"
            onClick={() => {
              setFollowing(true);
              scrollTo(entries.length * rowHeight);
            }}
          >
            Resume auto-scroll
          </Button>
        )}
      </div>
    </section>
  );
}
