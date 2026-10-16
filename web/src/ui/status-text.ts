import type { StatusInfo } from "@/lib/api";
import { transferred } from "@/lib/format";

export type Tone = "live" | "warn" | "danger" | "outline";

const TONES: Record<string, Tone> = {
  queued: "outline",
  pending: "outline",
  claimed: "warn",
  paused: "warn",
  needs_human: "warn",
  "needs-you": "warn",
  approved: "live",
  merged: "live",
  pass: "live",
  ready: "live",
  approve: "live",
  done: "live",
  cancelled: "outline",
  changes: "warn",
  blocked: "danger",
  escalate: "danger",
  connected: "live",
  disconnected: "danger",
  running: "live",
  starting: "warn",
  // The node is downloading the source's library file: on its way up, like starting.
  fetching: "warn",
  stopping: "warn",
  connecting: "warn",
  reconnecting: "warn",
  degraded: "warn",
  failed: "danger",
  stopped: "outline",
  idle: "outline",
  configured: "outline",
  "not-sending": "outline",
};

const LABELS: Record<string, string> = {
  needs_human: "Needs you",
  "needs-you": "Needs you",
  pass: "PASS",
  ready: "READY",
  approve: "APPROVE",
  changes: "CHANGES",
  blocked: "BLOCKED",
  escalate: "ESCALATE",
  idle: "Ended",
  configured: "Stopped",
  "not-sending": "Running",
};

/**
 * The state a source is shown in. The API reports a source that encodes while none of
 * its destinations is running as `running` with the reason `no-destinations`. That is
 * true of the worker, and reads as healthy when nothing leaves the gateway, so it is
 * shown as its own state: still called Running, with a hollow dot and no green fill.
 */
export function sourceState(summary: string, reason?: string | null): string {
  return summary.toLowerCase() === "running" && reason === "no-destinations"
    ? "not-sending"
    : summary;
}

export function statusTone(state: string): Tone {
  return TONES[state.toLowerCase()] ?? "outline";
}

export function statusLabel(state: string): string {
  const key = state.toLowerCase();
  return LABELS[key] ?? key.charAt(0).toUpperCase() + key.slice(1);
}

/**
 * A GStreamer bus message reads "b1-sink: Failed to write to SRT socket: Error on SRT
 * socket: Connection timeout". The last part is the cause; the full text is shown
 * where there is room for it.
 */
function shortError(error: string | null | undefined): string | null {
  if (!error) return null;
  const parts = error.trim().split(": ");
  const cause = parts[parts.length - 1] || error.trim();
  return cause.length > 120 ? `${cause.slice(0, 117)}…` : cause;
}

/** Only the "retrying in N s" part, for a line that already shows the full error. */
export function retryDetail(info: StatusInfo, now: number): string | null {
  return retryText(info.nextRetryAt, now);
}

function retryText(nextRetryAt: string | undefined, now: number): string | null {
  if (!nextRetryAt) return null;
  const at = Date.parse(nextRetryAt);
  if (Number.isNaN(at)) return null;
  const seconds = Math.ceil((at - now) / 1000);
  return seconds > 0 ? `retrying in ${seconds} s` : "retrying now";
}

/**
 * Why a node has no file to give a source's worker: it could not fetch the library
 * file, or it has the file and cannot read it. Every one of them is tried again.
 */
const MEDIA_FAULTS: Record<string, string> = {
  "media-not-found": "Media file not found in the library",
  "media-checksum-mismatch": "Downloaded file did not match its checksum",
  "media-disk-full": "Node disk is full",
  "media-api-unreachable": "Node cannot reach the API",
  "media-write-failed": "Node could not write the file",
  "media-unplayable": "Node cannot read the media file",
};

/**
 * A source that waits for its library file, in two parts that are always both there:
 * what is happening, and how far it is or when it is tried again. A table shows them
 * on a line each, so the row keeps its height while the numbers change. Null for any
 * other status.
 */
export function mediaStatus(info: StatusInfo, now: number): [string, string] | null {
  if (info.reason === "media-fetching") {
    return [
      "Fetching media",
      info.mediaFetch
        ? transferred(info.mediaFetch.receivedBytes, info.mediaFetch.totalBytes)
        : "starting…",
    ];
  }
  const fault = info.reason ? MEDIA_FAULTS[info.reason] : undefined;
  return fault ? [fault, retryText(info.nextRetryAt, now) ?? "retrying…"] : null;
}

/**
 * The sentence shown next to a status that is not simply running: which destination,
 * what went wrong, and when the next attempt is. Null when there is nothing to add.
 */
export function statusDetail(info: StatusInfo, now: number): string | null {
  const retry = retryText(info.nextRetryAt, now);
  const error = shortError(info.lastError);
  const join = (...parts: (string | null | undefined)[]) => parts.filter(Boolean).join(" · ");
  const media = mediaStatus(info, now);
  if (media) return join(...media);
  switch (info.reason) {
    case "node-offline":
      return "Node offline. The gateway is not reporting, so the stream may still be up.";
    case "worker-exited":
      return join("Worker exited", retry);
    case "crash-loop":
      return join("Worker keeps exiting", retry);
    case "apply-failed":
      return join("Could not start", error);
    case "destination-reconnecting":
      // The retry time exists only during the backoff. Without a stand-in the sentence
      // gets shorter and longer once a second, and in a table cell that rewraps the row.
      return join(
        info.destinationName ? `${info.destinationName} is not connected` : "Not connected",
        error,
        retry ?? "trying to connect…",
      );
    case "destination-failed":
      return join(
        info.destinationName ? `${info.destinationName} failed` : "Destination failed",
        error,
      );
    case "ended":
      return "The file reached its end.";
    case "no-destinations":
      return "Encoding, with no destination running.";
    case "no-stats-yet":
      return "Waiting for the first report from the worker.";
    default:
      return join(error, retry) || null;
  }
}
