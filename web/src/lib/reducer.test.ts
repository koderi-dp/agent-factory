import { describe, expect, it } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import {
  activitySchema,
  eventSchema,
  eventTypes,
  keys,
  stateSchema,
  type RepoEvent,
  type RepoState,
} from "./api";
import { reduce } from "./reducer";
import { applyEvent } from "./sse";

function state(last_event_id = 0): RepoState {
  return stateSchema.parse({
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
}
function event(type: (typeof eventTypes)[number], next = state(1)): RepoEvent {
  return eventSchema.parse({
    id: next.last_event_id,
    type,
    job_id: null,
    ts: "2026-10-16T12:00:00Z",
    payload: {},
    state: next,
    detail: null,
  });
}

describe("pure reducer", () => {
  it.each(eventTypes)("applies %s from its authoritative snapshot", (type) => {
    const before = Object.freeze(state());
    const next = state(2);
    next.backlog = [{ index: 0, key: "k", title: "added", details: "", priority: "normal", status: "ready" }];
    next.config.merge = "auto";
    expect(reduce(before, { type: "durable", payload: event(type, next) })).toEqual(next);
    expect(before.last_event_id).toBe(0);
    expect(before.backlog).toEqual([]);
  });
  it("ignores duplicates and out-of-order snapshots, including stale replay", () => {
    const current = state(10);
    for (const n of [1, 9, 10])
      expect(reduce(current, { type: "durable", payload: event("job.state", state(n)) })).toBe(
        current,
      );
  });
  it("initialises absent cache and clears on reset", () => {
    const next = state(5);
    expect(reduce(undefined, { type: "durable", payload: event("config", next) })).toEqual(next);
    expect(reduce(next, { type: "reset" })).toBeUndefined();
  });
  it("merges activity by attempt and timestamp without advancing durable cursor", () => {
    const payload = activitySchema.parse({
      attempt_id: 1,
      text: "running",
      at: "2026-10-16T12:00:00Z",
    });
    const before = state(2);
    before.activity = [{ attempt_id: 1, text: null, at: null }];
    const next = reduce(before, { type: "activity", payload })!;
    expect(next.last_event_id).toBe(2);
    expect(next.activity).toEqual([payload]);
    expect(before.activity).toEqual([{ attempt_id: 1, text: null, at: null }]);
    expect(
      reduce(next, { type: "activity", payload: { ...payload, at: "2026-10-15T12:00:00Z" } }),
    ).toBe(next);
    const snapshot = state(3);
    snapshot.activity = [{ ...payload, text: "stale", at: "2026-10-15T12:00:00Z" }];
    expect(
      reduce(next, { type: "durable", payload: event("attempt.state", snapshot) })?.activity,
    ).toEqual([payload]);
    expect(reduce(undefined, { type: "activity", payload })).toBeUndefined();
  });
  it("removes finished attempts' ephemeral activity", () => {
    const before = state(2);
    before.activity = [{ attempt_id: 1, text: "running", at: "2026-10-16T12:00:00Z" }];
    const finished = reduce(before, { type: "durable", payload: event("attempt.state", state(3)) });
    expect(finished?.activity).toEqual([]);
    expect(reduce(finished, { type: "activity", payload: before.activity[0] })).toBe(finished);
  });
  it("rejects invalid wire data", () => {
    expect(eventSchema.safeParse({ type: "job.state", id: -1 }).success).toBe(false);
    expect(stateSchema.safeParse({ ...state(), jobs: [{ state: "invented" }] }).success).toBe(
      false,
    );
  });
});

describe("Query cache", () => {
  it("uses the reducer and removes deleted job detail without a fetch", () => {
    const client = new QueryClient();
    client.setQueryData(keys.state("r"), state());
    client.setQueryData(keys.job("r", "deleted"), { job: { id: "deleted" } });
    applyEvent(client, "r", event("job.state", state(2)));
    expect(client.getQueryData(keys.state("r"))).toEqual(state(2));
    expect(client.getQueryData(keys.job("r", "deleted"))).toBeUndefined();
    applyEvent(client, "r", event("backlog", state(1)));
    expect(client.getQueryData<RepoState>(keys.state("r"))?.last_event_id).toBe(2);
    client.clear();
  });
});
