import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { api, keys, type StartedBacklogItem } from "@/lib/api";
import { PageShell } from "@/ui/page-shell";
import { Button } from "@/ui/primitives/button";
import { Input } from "@/ui/primitives/input";
import { Select } from "@/ui/primitives/select";
import { Badge } from "@/ui/primitives/badge";
import { Tabs, TabsList, TabsTrigger } from "@/ui/primitives/tabs";
import { BacklogKanban } from "./BacklogKanban";
import { metaChange, priorityLabel, statusLabel, type BacklogTarget } from "./model";

const textareaClass = "min-h-24 w-full rounded-md border border-border bg-inset p-3 text-sm";

function EditForm({
  item,
  busy,
  onSave,
  onCancel,
}: {
  item: { key: string; title: string; details: string };
  busy: boolean;
  onSave: (text: string, details: string) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState(item.title);
  const [body, setBody] = useState(item.details);
  return (
    <form
      className="grid gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(text, body);
      }}
    >
      <label>
        Title
        <Input required maxLength={300} value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      <label>
        Details
        <textarea
          className={textareaClass}
          maxLength={20000}
          value={body}
          onChange={(e) => setBody(e.target.value)}
        />
      </label>
      <div className="flex gap-2">
        <Button loading={busy} disabled={!text.trim() || busy}>
          Save
        </Button>
        <Button type="button" variant="outline" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

export function Backlog({ repo }: { repo: string }) {
  const state = useQuery({ queryKey: keys.state(repo), queryFn: () => api.state(repo) });
  const [title, setTitle] = useState("");
  const [details, setDetails] = useState("");
  const [pipeline, setPipeline] = useState("");
  const [drag, setDrag] = useState<string>();
  const [editing, setEditing] = useState<string>();
  const [waiting, setWaiting] = useState<typeof state.data>();
  const mutation = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.backlog(repo, body),
    onMutate: () => setWaiting(state.data),
    onError: () => setWaiting(undefined),
    // An edit that leaves the text as it was changes nothing, so no live update
    // would ever release the controls: release them from the response instead.
    onSuccess: (result) => {
      if (!("backlog" in result)) return;
      setWaiting((snapshot) =>
        snapshot &&
        JSON.stringify(snapshot.backlog) === JSON.stringify(result.backlog) &&
        JSON.stringify(snapshot.started) === JSON.stringify(result.started)
          ? undefined
          : snapshot,
      );
    },
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
    (waiting !== undefined &&
      JSON.stringify(waiting.backlog) === JSON.stringify(data?.backlog) &&
      JSON.stringify(waiting.started) === JSON.stringify(data?.started));
  const selected =
    data && Object.hasOwn(data.config.pipelines, pipeline)
      ? pipeline
      : (data?.config.defaultPipeline ?? "");
  function move(key: string, to: number) {
    const index = data?.backlog.findIndex((item) => item.key === key) ?? -1;
    if (index >= 0 && index !== to && !busy) mutation.mutate({ action: "reorder", index, to, key });
  }
  function save(key: string, text: string, details: string) {
    if (busy) return;
    mutation.mutate(
      { action: "edit", key, text, details },
      { onSuccess: () => setEditing(undefined) },
    );
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
                  draggable={!busy && editing !== item.key}
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
                  {editing === item.key ? (
                    <EditForm
                      item={item}
                      busy={busy}
                      onSave={(text, details) => save(item.key, text, details)}
                      onCancel={() => setEditing(undefined)}
                    />
                  ) : (
                    <>
                      <h2 className="font-medium">{item.title}</h2>
                      <div className="mt-1 flex gap-2">
                        <Badge variant="outline">{priorityLabel(item.priority)}</Badge>
                        <Badge>{statusLabel(item.status)}</Badge>
                      </div>
                      <p className="whitespace-pre-wrap text-sm text-muted-foreground">
                        {item.details}
                      </p>
                    </>
                  )}
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
                      aria-label={`Edit ${item.title}`}
                      disabled={busy}
                      onClick={() => setEditing(item.key)}
                    >
                      Edit
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
          {(data.started ?? []).length > 0 && (
            <section className="grid gap-3" aria-label="Started items">
              <h2 className="text-lg font-medium">Started</h2>
              <ul className="grid gap-3">
                {(data.started ?? []).map((item: StartedBacklogItem) => (
                  <li key={item.key} className="rounded-md border border-border bg-card p-4">
                    {editing === item.key ? (
                      <EditForm
                        item={item}
                        busy={busy}
                        onSave={(text, details) => save(item.key, text, details)}
                        onCancel={() => setEditing(undefined)}
                      />
                    ) : (
                      <>
                        <h3 className="font-medium">{item.title}</h3>
                        <p className="whitespace-pre-wrap text-sm text-muted-foreground">
                          {item.details}
                        </p>
                        <div className="mt-3 flex gap-2">
                          <a
                            className="text-sm underline"
                            href={`#/repo/${encodeURIComponent(repo)}/job/${encodeURIComponent(item.job)}`}
                          >
                            Open job
                          </a>
                          <Button
                            variant="outline"
                            aria-label={`Edit ${item.title}`}
                            disabled={busy}
                            onClick={() => setEditing(item.key)}
                          >
                            Edit
                          </Button>
                        </div>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </PageShell>
  );
}
