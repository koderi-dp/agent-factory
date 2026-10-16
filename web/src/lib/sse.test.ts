import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { api, keys, stateSchema } from "./api";
import { connectLog, connectOverview, connectRepo } from "./sse";

class Source {
  static all: Source[] = [];
  listeners = new Map<string, ((event: MessageEvent) => void)[]>();
  onopen?: () => void;
  onerror?: () => void;
  closed = false;
  constructor(public url: string) {
    Source.all.push(this);
  }
  addEventListener(type: string, callback: (event: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), callback]);
  }
  emit(type: string, data: unknown = {}) {
    for (const callback of this.listeners.get(type) ?? [])
      callback({ data: JSON.stringify(data) } as MessageEvent);
  }
  close() {
    this.closed = true;
  }
}
const snapshot = (last_event_id: number) =>
  stateSchema.parse({
    jobs: [],
    executions: [],
    backlog: [],
    activity: [],
    last_event_id,
    config: {
      version: 2,
      engine: ">=1",
      defaultPipeline: "full",
      pipelines: {},
      maxRounds: 3,
      merge: "verify",
      permissions: "auto",
      concurrency: { perJob: 4 },
      timeouts: { executionMinutes: 60 },
      sensitivePaths: [],
    },
  });
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  Source.all = [];
});

describe("SSE lifecycle", () => {
  it("seeds initial cursor and refetches once on reset before reconnecting", async () => {
    vi.stubGlobal("EventSource", Source);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const fetch = vi
      .spyOn(api, "state")
      .mockResolvedValueOnce(snapshot(4))
      .mockResolvedValueOnce(snapshot(20));
    const close = connectRepo(client, "repo space");
    await vi.waitFor(() => expect(Source.all).toHaveLength(1));
    expect(Source.all[0].url).toContain("repo%20space/events?after=4");
    Source.all[0].emit("reset", { last_event_id: 15 });
    await vi.waitFor(() => expect(Source.all).toHaveLength(2));
    expect(Source.all[0].closed).toBe(true);
    expect(Source.all[1].url).toContain("after=20");
    expect(client.getQueryData(keys.state("repo space"))).toEqual(snapshot(20));
    expect(fetch).toHaveBeenCalledTimes(2);
    close();
    expect(Source.all[1].closed).toBe(true);
    client.clear();
  });
  it("does not open a stream after unmount during the initial fetch", async () => {
    vi.stubGlobal("EventSource", Source);
    const client = new QueryClient();
    vi.spyOn(api, "state").mockResolvedValue(snapshot(4));
    connectRepo(client, "r")();
    await vi.waitFor(() => expect(client.getQueryData(keys.state("r"))).toBeDefined());
    expect(Source.all).toHaveLength(0);
    client.clear();
  });
  it("refetches overview on reconnect and accepts overview snapshots", async () => {
    vi.stubGlobal("EventSource", Source);
    const client = new QueryClient();
    const fetch = vi.spyOn(api, "overview").mockResolvedValue({ repos: [] });
    const close = connectOverview(client);
    Source.all[0].onopen?.();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    await vi.waitFor(() => expect(client.getQueryState(keys.overview)?.fetchStatus).toBe("idle"));
    Source.all[0].onopen?.();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    Source.all[0].emit("overview", { repos: [] });
    expect(client.getQueryData(keys.overview)).toEqual({ repos: [] });
    close();
    expect(Source.all[0].closed).toBe(true);
    client.clear();
  });
  it("deduplicates replayed log batches and closes on end", () => {
    vi.stubGlobal("EventSource", Source);
    const onBatch = vi.fn();
    const close = connectLog("r", 1, 10, onBatch);
    const source = Source.all[0];
    source.emit("log", { text: "old", offset: 10 });
    source.emit("log", { text: "new", offset: 20 });
    source.emit("log", { text: "new", offset: 20 });
    expect(onBatch).toHaveBeenCalledExactlyOnceWith({ text: "new", offset: 20 });
    source.emit("end", { offset: 20 });
    expect(source.closed).toBe(true);
    close();
  });
});
