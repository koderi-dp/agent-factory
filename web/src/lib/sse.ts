import type { QueryClient } from "@tanstack/react-query";
import { z } from "zod";
import {
  activitySchema,
  api,
  eventSchema,
  eventTypes,
  keys,
  overviewSchema,
  type JobDetail,
  type RepoEvent,
  type RepoState,
} from "./api";
import { reduce } from "./reducer";

export function applyEvent(client: QueryClient, repo: string, event: RepoEvent) {
  const current = client.getQueryData<RepoState>(keys.state(repo));
  if (current && event.state.last_event_id < current.last_event_id) return;
  client.setQueryData<RepoState>(keys.state(repo), (state) =>
    reduce(state, { type: "durable", payload: event }),
  );
  if (event.job_id) {
    const key = keys.job(repo, event.job_id);
    if (event.detail) client.setQueryData<JobDetail>(key, event.detail);
    else client.removeQueries({ queryKey: key, exact: true });
  }
  for (const query of client.getQueryCache().findAll({ queryKey: ["repo", repo, "job"] })) {
    if (!event.state.jobs.some((job) => job.id === query.queryKey[3])) {
      client.removeQueries({ queryKey: query.queryKey, exact: true });
    }
  }
}

type Connection = "connecting" | "connected" | "disconnected";
/** Native EventSource retains Last-Event-ID on automatic reconnect. `after` seeds first connect. */
export function connectRepo(
  client: QueryClient,
  repo: string,
  onConnection?: (state: Connection) => void,
) {
  let source: EventSource | undefined;
  let closed = false;
  let recovering = false;
  let retry: ReturnType<typeof setTimeout> | undefined;
  const queryKey = keys.state(repo);

  const reset = async () => {
    if (closed || recovering) return;
    recovering = true;
    source?.close();
    try {
      await client.cancelQueries({ queryKey });
      await client.invalidateQueries({ queryKey, refetchType: "none" });
      await client.fetchQuery({ queryKey, queryFn: () => api.state(repo), staleTime: 0 });
      await client.invalidateQueries({ queryKey: ["repo", repo, "job"] });
      recovering = false;
      open();
    } catch {
      recovering = false;
      if (!closed) {
        onConnection?.("disconnected");
        retry = setTimeout(() => void reset(), 1000);
      }
    }
  };
  const open = () => {
    if (closed) return;
    const after = client.getQueryData<RepoState>(queryKey)?.last_event_id ?? 0;
    onConnection?.("connecting");
    source = new EventSource(`/api/repos/${encodeURIComponent(repo)}/events?after=${after}`);
    source.onopen = () => onConnection?.("connected");
    source.onerror = () => onConnection?.("disconnected");
    source.addEventListener("reset", () => void reset());
    for (const type of eventTypes)
      source.addEventListener(type, (message) => {
        try {
          const event = eventSchema.parse(JSON.parse((message as MessageEvent).data));
          if (event.type !== type) throw new Error("Mismatched SSE type");
          applyEvent(client, repo, event);
        } catch {
          void reset();
        }
      });
    source.addEventListener("activity", (message) => {
      try {
        const payload = activitySchema.parse(JSON.parse((message as MessageEvent).data));
        client.setQueryData<RepoState>(queryKey, (state) =>
          reduce(state, { type: "activity", payload }),
        );
      } catch {
        void reset();
      }
    });
  };
  void client
    .ensureQueryData({ queryKey, queryFn: () => api.state(repo) })
    .then(open)
    .catch(() => void reset());
  return () => {
    closed = true;
    source?.close();
    clearTimeout(retry);
  };
}

export function connectOverview(client: QueryClient, onConnection?: (state: Connection) => void) {
  const source = new EventSource("/api/overview/stream");
  source.onopen = () => {
    onConnection?.("connected");
    // Overview has no replay; refetch on every reconnect.
    void client.fetchQuery({ queryKey: keys.overview, queryFn: api.overview, staleTime: 0 });
  };
  source.onerror = () => onConnection?.("disconnected");
  source.addEventListener("overview", (message) => {
    const parsed = overviewSchema.safeParse(JSON.parse((message as MessageEvent).data));
    if (parsed.success) client.setQueryData(keys.overview, parsed.data);
  });
  return () => source.close();
}

const logSchema = z.object({ text: z.string(), offset: z.number().int().nonnegative() });
export function connectLog(
  repo: string,
  attempt: number,
  offset: number,
  onBatch: (batch: z.infer<typeof logSchema>) => void,
) {
  let cursor = offset;
  const source = new EventSource(
    `/api/repos/${encodeURIComponent(repo)}/attempts/${attempt}/stream?offset=${offset}`,
  );
  source.addEventListener("log", (message) => {
    const batch = logSchema.parse(JSON.parse((message as MessageEvent).data));
    if (batch.offset <= cursor) return;
    cursor = batch.offset;
    onBatch(batch);
  });
  source.addEventListener("end", () => source.close());
  return () => source.close();
}
