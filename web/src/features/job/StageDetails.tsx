import type { ReactNode } from "react";
import {
  latestAttempt,
  stageTiming,
  type Attempt,
  type Execution,
  type Step,
} from "@/features/board/model";
import { clockTime, duration } from "@/lib/format";
import { useNow } from "./use-now";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-3">
      <dt className="w-24 shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

export function StageDetails({
  step,
  entry,
  execution,
  attempts,
}: {
  step?: Step;
  entry?: Step["executions"][number];
  execution?: Execution;
  attempts: Attempt[];
}) {
  const mine = execution ? attempts.filter((a) => a.execution_id === execution.id) : [];
  const attempt = execution ? latestAttempt(execution, attempts) : undefined;
  const ticking = !!attempt && !!attempt.t_launch && !attempt.t_exit;
  const now = useNow(ticking);
  const timing = stageTiming(attempt, now);
  const role = execution?.uses ?? entry?.uses ?? entry?.id ?? step?.id ?? "—";
  const type = execution?.type ?? step?.type;
  const provider = execution?.provider ?? entry?.provider;
  const profile = entry?.profiles?.[entry.provider ?? ""];
  const configured = execution ? "" : " (configured)";
  const model = execution?.model ?? profile?.model;
  const effort = execution?.effort ?? profile?.effort;
  return (
    <dl aria-label="Stage details" className="mb-4 space-y-1 text-sm">
      <Row label="Role">
        {role}
        {type ? ` · ${type}` : ""}
      </Row>
      <Row label="Provider">{provider ? `${provider}${configured}` : "—"}</Row>
      <Row label="Model">{model ? `${model}${configured}` : "—"}</Row>
      <Row label="Effort">{effort ? `${effort}${configured}` : "—"}</Row>
      <Row label="Round">{execution?.round ?? "—"}</Row>
      <Row label="Attempt">
        {attempt
          ? `${attempt.n}${attempt.kind !== "fresh" ? ` (${attempt.kind})` : ""}${
              mine.length > 1 ? ` of ${mine.length}` : ""
            }`
          : "—"}
      </Row>
      <Row label="Started">{clockTime(timing.start)}</Row>
      <Row label="Finished">{clockTime(timing.finish)}</Row>
      <Row label="Duration">{duration(timing.seconds)}</Row>
      <Row label="State">{execution?.state ?? "—"}</Row>
    </dl>
  );
}
