import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { api, keys } from "@/lib/api";
import { PageShell } from "@/ui/page-shell";
import { Button } from "@/ui/primitives/button";
import { Input } from "@/ui/primitives/input";
import { Select } from "@/ui/primitives/select";
import { Badge } from "@/ui/primitives/badge";
import { Tabs, TabsList, TabsTrigger } from "@/ui/primitives/tabs";
import { BacklogKanban } from "./BacklogKanban";
import { metaChange, priorityLabel, statusLabel, type BacklogTarget } from "./model";

export function Backlog({ repo }: { repo: string }) {
  const state = useQuery({ queryKey: keys.state(repo), queryFn: () => api.state(repo) });
  const [title, setTitle] = useState("");
  const [details, setDetails] = useState("");
  const [pipeline, setPipeline] = useState("");
  const [drag, setDrag] = useState<string>();
  const [waiting, setWaiting] = useState<typeof state.data>();
  const mutation = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.backlog(repo, body),
    onMutate: () => setWaiting(state.data),
    onError: () => setWaiting(undefined),
  });
  const metaMutation = useMutation({
    mutationFn: (body: Parameters<typeof api.backlogMeta>[1]) => api.backlogMeta(repo, body),
    onMutate: () => setWaiting(state.data),
    onError: () => setWaiting(undefined),
  });
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { view?: string };
  const view = search.view === "board" ? "board" : "list";
  const data = state.data;
  // Ignore activity and unrelated events: only a changed backlog releases the controls.
  const busy =
    mutation.isPending ||
    metaMutation.isPending ||
    (waiting !== undefined && JSON.stringify(waiting.backlog) === JSON.stringify(data?.backlog));
  const selected =
    data && Object.hasOwn(data.config.pipelines, pipeline)
      ? pipeline
      : (data?.config.defaultPipeline ?? "");
  function move(key: string, to: number) {
    const index = data?.backlog.findIndex((item) => item.key === key) ?? -1;
    if (index >= 0 && index !== to && !busy) mutation.mutate({ action: "reorder", index, to, key });
  }
  function moveMeta(item: Parameters<typeof metaChange>[0], target: BacklogTarget) {
    const body = metaChange(item, target);
    if (body && !busy) metaMutation.mutate(body);
  }
  return (
    <PageShell>
      <h1 className="text-2xl font-medium">Backlog</h1>
      {state.isPending && <p role="status">Loading backlog…</p>}
      {state.error && <p role="alert">{state.error.message}</p>}
      {mutation.error && (
        <p role="alert" className="text-danger">
          {mutation.error.message}
        </p>
      )}
      {metaMutation.error && (
        <p role="alert" className="text-danger">
          {metaMutation.error.message}
        </p>
      )}
      {data && (
        <>
          <label>
            Pipeline
            <Select value={selected} onChange={(e) => setPipeline(e.target.value)}>
              {Object.keys(data.config.pipelines).map((name) => (
                <option key={name}>{name}</option>
              ))}
            </Select>
          </label>
          <form
            className="grid gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              mutation.mutate(
                { action: "add", text: title, details },
                {
                  onSuccess: () => {
                    setTitle("");
                    setDetails("");
                  },
                },
              );
            }}
          >
            <label>
              Title
              <Input
                required
                maxLength={300}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>
            <label>
              Details
              <textarea
                className="min-h-24 w-full rounded-md border border-border bg-inset p-3 text-sm"
                maxLength={20000}
                value={details}
                onChange={(e) => setDetails(e.target.value)}
              />
            </label>
            <Button loading={busy} disabled={!title.trim() || busy}>
              Add item
            </Button>
          </form>
          <Tabs
            value={view}
            onValueChange={(next) =>
              void navigate({
                to: ".",
                search: next === "board" ? { view: "board" } : {},
              } as never)
            }
          >
            <TabsList aria-label="Backlog view">
              <TabsTrigger value="list">List</TabsTrigger>
              <TabsTrigger value="board">Board</TabsTrigger>
            </TabsList>
          </Tabs>
          {view === "board" && (
            <BacklogKanban backlog={data.backlog} busy={busy} onMove={moveMeta} />
          )}
          {view === "list" && !data.backlog.length && (
            <p className="text-muted-foreground">No queued items.</p>
          )}
          {view === "list" && (
            <ol className="grid gap-3">
              {data.backlog.map((item, i) => (
                <li
                  key={item.key}
                  draggable={!busy}
                  onDragStart={(e) => {
                    setDrag(item.key);
                    e.dataTransfer.setData("text/plain", item.key);
                  }}
                  onDragEnd={() => setDrag(undefined)}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => {
                    e.preventDefault();
                    if (drag) move(drag, i);
                    setDrag(undefined);
                  }}
                  className="rounded-md border border-border bg-card p-4"
                >
                  <h2 className="font-medium">{item.title}</h2>
                  <div className="mt-1 flex gap-2">
                    <Badge variant="outline">{priorityLabel(item.priority)}</Badge>
                    <Badge>{statusLabel(item.status)}</Badge>
                  </div>
                  <p className="whitespace-pre-wrap text-sm text-muted-foreground">
                    {item.details}
                  </p>
                  <div className="mt-3 flex gap-2">
                    <Button
                      disabled={busy}
                      onClick={() =>
                        mutation.mutate({
                          action: "start",
                          index: item.index,
                          key: item.key,
                          pipeline: selected,
                        })
                      }
                    >
                      Start
                    </Button>
                    <Button
                      variant="outline"
                      aria-label={`Move ${item.title} up`}
                      disabled={!i || busy}
                      onClick={() => move(item.key, i - 1)}
                    >
                      ↑
                    </Button>
                    <Button
                      variant="outline"
                      aria-label={`Move ${item.title} down`}
                      disabled={i === data.backlog.length - 1 || busy}
                      onClick={() => move(item.key, i + 1)}
                    >
                      ↓
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={busy}
                      onClick={() =>
                        mutation.mutate({ action: "remove", index: item.index, key: item.key })
                      }
                    >
                      Remove
                    </Button>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </PageShell>
  );
}
