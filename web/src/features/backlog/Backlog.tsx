import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys } from "@/lib/api";
import { PageShell } from "@/ui/page-shell";
import { Button } from "@/ui/primitives/button";
import { Input } from "@/ui/primitives/input";
import { Select } from "@/ui/primitives/select";

export function Backlog({ repo }: { repo: string }) {
  const state = useQuery({ queryKey: keys.state(repo), queryFn: () => api.state(repo) });
  const [title, setTitle] = useState("");
  const [details, setDetails] = useState("");
  const [pipeline, setPipeline] = useState("");
  const [drag, setDrag] = useState<string>();
  const mutation = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.backlog(repo, body),
  });
  const data = state.data;
  const selected =
    data && Object.hasOwn(data.config.pipelines, pipeline)
      ? pipeline
      : (data?.config.defaultPipeline ?? "");
  function move(key: string, to: number) {
    const index = data?.backlog.findIndex((item) => item.key === key) ?? -1;
    if (index >= 0 && index !== to && !mutation.isPending)
      mutation.mutate({ action: "reorder", index, to });
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
            <Button loading={mutation.isPending} disabled={!title.trim() || mutation.isPending}>
              Add item
            </Button>
          </form>
          {!data.backlog.length && <p className="text-muted-foreground">No queued items.</p>}
          <ol className="grid gap-3">
            {data.backlog.map((item, i) => (
              <li
                key={item.key}
                draggable={!mutation.isPending}
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
                <p className="whitespace-pre-wrap text-sm text-muted-foreground">{item.details}</p>
                <div className="mt-3 flex gap-2">
                  <Button
                    disabled={mutation.isPending}
                    onClick={() =>
                      mutation.mutate({ action: "start", index: item.index, pipeline: selected })
                    }
                  >
                    Start
                  </Button>
                  <Button
                    variant="outline"
                    aria-label={`Move ${item.title} up`}
                    disabled={!i || mutation.isPending}
                    onClick={() => move(item.key, i - 1)}
                  >
                    ↑
                  </Button>
                  <Button
                    variant="outline"
                    aria-label={`Move ${item.title} down`}
                    disabled={i === data.backlog.length - 1 || mutation.isPending}
                    onClick={() => move(item.key, i + 1)}
                  >
                    ↓
                  </Button>
                  <Button
                    variant="ghost"
                    disabled={mutation.isPending}
                    onClick={() => mutation.mutate({ action: "remove", index: item.index })}
                  >
                    Remove
                  </Button>
                </div>
              </li>
            ))}
          </ol>
        </>
      )}
    </PageShell>
  );
}
