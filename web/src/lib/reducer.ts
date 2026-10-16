import type { ActivityEvent, RepoEvent, RepoState } from "./api";

export type DataEvent =
  | { type: "activity"; payload: ActivityEvent }
  | { type: "reset" }
  | { type: "durable"; payload: RepoEvent };

/** Snapshots are captured with their watermark; never roll the cache backwards. */
export function reduce(state: RepoState | undefined, event: DataEvent): RepoState | undefined {
  if (event.type === "reset") return undefined;
  if (event.type === "activity") {
    if (!state) return state;
    const incoming = event.payload;
    const previous = state.activity.find((item) => item.attempt_id === incoming.attempt_id);
    if (!previous) return state; // A late activity cannot resurrect a finished attempt.
    if (previous?.at && (!incoming.at || incoming.at <= previous.at)) return state;
    return {
      ...state,
      activity: [
        ...state.activity.filter((item) => item.attempt_id !== incoming.attempt_id),
        incoming,
      ],
    };
  }
  const incoming = event.payload.state;
  if (state && incoming.last_event_id <= state.last_event_id) return state;
  // An ephemeral activity can be newer than a snapshot delivered on the same connection.
  const activity = incoming.activity.map((item) => {
    const previous = state?.activity.find((value) => value.attempt_id === item.attempt_id);
    return previous?.at && (!item.at || previous.at > item.at) ? previous : item;
  });
  return { ...incoming, activity };
}
