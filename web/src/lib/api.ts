// TODO(Session 3.2, plan §13.2): replace this scaffold with the API response schemas.
export type StatusInfo = {
  reason?: string | null;
  nextRetryAt?: string;
  mediaFetch?: { receivedBytes: number; totalBytes: number };
  lastError?: string | null;
  destinationName?: string | null;
};
