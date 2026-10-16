import { z } from "zod";

const id = z.number().int().nonnegative();
const nullableText = z.string().nullable();
const object = z.record(z.string(), z.unknown());
export const jobSchema = z.looseObject({
  id: z.string(),
  seq: id,
  title: z.string(),
  pipeline_name: z.string(),
  frozen: object,
  state: z.enum(["queued", "running", "paused", "needs_human", "approved", "merged", "cancelled"]),
  pause_reason: nullableText,
  note: nullableText,
  current_step: nullableText,
  round: id,
  returns_used: id,
  port_base: id.nullable(),
  branch: z.string(),
  worktree: z.string(),
  base_head: nullableText,
  approved_head: nullableText,
  created_at: z.string(),
  updated_at: z.string(),
});
export const executionSchema = z.looseObject({
  id,
  job_id: z.string(),
  step_id: z.string(),
  exec_id: z.string(),
  round: id,
  type: z.enum(["task", "review", "approval"]),
  uses: z.string(),
  provider: z.string(),
  model: z.string(),
  effort: z.string(),
  state: z.enum(["pending", "claimed", "running", "done", "failed", "stopped", "superseded"]),
  result: nullableText,
  head_before: nullableText,
  head_after: nullableText,
  report_path: nullableText,
  created_at: z.string(),
});
export const attemptSchema = z.looseObject({
  id,
  execution_id: id,
  n: id,
  kind: z.enum(["fresh", "resume", "contract_retry"]),
  state: z.enum([
    "launching",
    "running",
    "exited",
    "valid",
    "invalid",
    "stopped",
    "lost",
    "failed",
  ]),
  session_id: nullableText,
  wrapper_pid: id.nullable(),
  wrapper_ctime: z.number().nullable(),
  exit_code: z.number().int().nullable(),
  error_class: nullableText,
  error_detail: nullableText,
  dir: z.string(),
  log_offset: id,
  last_activity: nullableText,
  last_activity_at: nullableText,
  t_queued: nullableText,
  t_launch: nullableText,
  t_first_event: nullableText,
  t_exit: nullableText,
  t_validated: nullableText,
  packet_bytes: id.nullable(),
  usage: object.nullable(),
});
const profile = z.object({ model: z.string(), effort: z.string() });
const configuredExecution = z.object({
  id: z.string(),
  uses: z.string(),
  provider: z.enum(["codex", "claude"]),
  profiles: z.record(z.string(), profile),
  timeoutMinutes: z.number().positive().nullable().optional(),
  maxBudgetUsd: z.number().positive().nullable().optional(),
});
const stepFields = {
  id: z.string(),
  label: nullableText.optional(),
  onChanges: nullableText.optional(),
};
export const configSchema = z.object({
  version: z.literal(2),
  engine: z.string(),
  defaultPipeline: z.string(),
  pipelines: z.record(
    z.string(),
    z.array(
      z.union([
        configuredExecution.extend(stepFields),
        z.object({ ...stepFields, parallel: z.array(configuredExecution) }),
      ]),
    ),
  ),
  maxRounds: z.number().int(),
  merge: z.enum(["auto", "verify"]),
  permissions: z.enum(["auto", "bypass"]),
  concurrency: z.object({ perJob: id }),
  timeouts: z.object({ executionMinutes: z.number() }),
  sensitivePaths: z.array(z.string()),
});
export const activitySchema = z.object({ attempt_id: id, text: nullableText, at: nullableText });
export const backlogSchema = z.array(
  z.object({ index: id, key: z.string(), title: z.string(), details: z.string() }),
);
export const legacyJobSchema = z.object({
  id: z.string(),
  title: z.string(),
  stage: z.string(),
  read_only: z.literal(true),
  reports: z.array(z.string()),
});
export type LegacyJob = z.infer<typeof legacyJobSchema>;
export const stateSchema = z.object({
  jobs: z.array(jobSchema),
  legacy_jobs: z.array(legacyJobSchema).optional(),
  executions: z.array(executionSchema),
  backlog: backlogSchema,
  config: configSchema,
  last_event_id: id,
  activity: z.array(activitySchema),
});
export const detailSchema = z.object({
  job: jobSchema,
  executions: z.array(executionSchema),
  attempts: z.array(attemptSchema),
});
export const overviewSchema = z.object({
  repos: z.array(
    z.object({
      id: z.string(),
      name: z.string(),
      path: z.string(),
      status: z.enum(["available", "missing", "version_mismatch"]),
      detail: nullableText,
      counts: z.object({ running: id, "needs-you": id, queued: id }),
    }),
  ),
});
export const eventTypes = [
  "job.state",
  "execution.state",
  "attempt.state",
  "usage",
  "note",
  "config",
  "backlog",
] as const;
export const eventSchema = z.object({
  id,
  type: z.enum(eventTypes),
  job_id: nullableText,
  ts: z.string(),
  payload: object,
  state: stateSchema,
  detail: detailSchema.nullable(),
});
export type RepoState = z.infer<typeof stateSchema>;
export type JobDetail = z.infer<typeof detailSchema>;
export type RepoEvent = z.infer<typeof eventSchema>;
export type ActivityEvent = z.infer<typeof activitySchema>;

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
}

export async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  const token = document.querySelector<HTMLMetaElement>('meta[name="factory-token"]')?.content;
  if (token) headers.set("X-Factory-Token", token);
  if (init.body) headers.set("Content-Type", "application/json");
  const response = await fetch(`/api${path}`, { ...init, headers });
  const data: unknown = await response.json();
  if (!response.ok)
    throw new ApiError(response.status, z.object({ detail: z.unknown() }).parse(data).detail);
  return schema.parse(data);
}
const repoPath = (repo: string) => `/repos/${encodeURIComponent(repo)}`;
export const keys = {
  overview: ["overview"] as const,
  state: (repo: string) => ["repo", repo, "state"] as const,
  job: (repo: string, job: string) => ["repo", repo, "job", job] as const,
};
export const api = {
  overview: () => request("/overview", overviewSchema),
  state: (repo: string) => request(`${repoPath(repo)}/state`, stateSchema),
  job: (repo: string, job: string) =>
    request(`${repoPath(repo)}/jobs/${encodeURIComponent(job)}`, detailSchema),
  config: (repo: string) => request(`${repoPath(repo)}/config`, configSchema),
  start: (repo: string, body: { title: string; details?: string; pipeline?: string }) =>
    request(`${repoPath(repo)}/jobs`, z.object({ id: nullableText, state: z.literal("queued") }), {
      method: "POST",
      body: JSON.stringify(body),
    }),
  action: (
    repo: string,
    job: string,
    action: "stop" | "resume" | "approve" | "rework" | "delete",
    body: Record<string, unknown> = {},
  ) =>
    request(
      `${repoPath(repo)}/jobs/${encodeURIComponent(job)}/${action}`,
      z.union([detailSchema, z.object({ message: z.string() })]),
      { method: "POST", body: JSON.stringify(body) },
    ),
  saveConfig: (repo: string, body: unknown) =>
    request(`${repoPath(repo)}/config`, configSchema, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  backlog: (repo: string, body: Record<string, unknown>) =>
    request(
      `${repoPath(repo)}/backlog`,
      z.union([
        z.object({ backlog: backlogSchema }),
        z.object({ id: nullableText, state: z.literal("queued") }),
      ]),
      { method: "POST", body: JSON.stringify(body) },
    ),
  log: (repo: string, attempt: number, offset = 0) =>
    request(
      `${repoPath(repo)}/attempts/${attempt}/log?offset=${offset}`,
      z.object({ text: z.string(), offset: id }),
    ),
};

// Compatibility shape used by the unchanged, copied srt-gateway status primitives.
export type StatusInfo = {
  reason?: string | null;
  nextRetryAt?: string;
  mediaFetch?: { receivedBytes: number; totalBytes: number };
  lastError?: string | null;
  destinationName?: string | null;
};

export const machineSchema = z.object({
  concurrency: z.object({ global: z.number().int().min(1).max(32) }),
  cli: z.object({ claude: nullableText, codex: nullableText }),
  ports: z.object({ range: z.string() }),
});
export const rolesSchema = z.object({
  roles: z.array(
    z.looseObject({
      name: z.string(),
      provider: z.string(),
      type: z.enum(["task", "review", "approval"]),
      source: z.string(),
      sha256: z.string(),
      status: z.string(),
      ejectedFrom: nullableText,
      outdated: z.boolean(),
    }),
  ),
});
export const settingsApi = {
  machine: () => request("/machine/settings", machineSchema),
  saveMachine: (body: z.infer<typeof machineSchema>) =>
    request("/machine/settings", machineSchema, { method: "PUT", body: JSON.stringify(body) }),
  validate: (repo: string, body: unknown) =>
    request(
      `${repoPath(repo)}/config/validate`,
      z.object({ pipelines: z.record(z.string(), z.array(z.unknown())) }),
      { method: "POST", body: JSON.stringify(body) },
    ),
  roles: (repo: string) => request(`${repoPath(repo)}/roles`, rolesSchema),
};
export async function report(repo: string, job: string, path: string): Promise<string> {
  const encoded = path.split("/").map(encodeURIComponent).join("/");
  const response = await fetch(
    `/api${repoPath(repo)}/jobs/${encodeURIComponent(job)}/files/${encoded}`,
  );
  if (!response.ok) {
    const data = z.object({ detail: z.unknown() }).parse(await response.json());
    throw new ApiError(response.status, data.detail);
  }
  return response.text();
}
