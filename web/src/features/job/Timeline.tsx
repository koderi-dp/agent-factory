import type { Attempt } from "@/features/board/model";
export function Timeline({ attempts }: { attempts: Attempt[] }) {
  return (
    <ol className="space-y-4">
      {attempts.map((a) => (
        <li key={a.id} className="rounded bg-inset p-3 text-sm">
          <strong>
            Execution {a.execution_id} · a{a.n} · {a.kind} · {a.state}
          </strong>
          <ol className="mt-2 space-y-1 font-mono text-xs text-muted-foreground">
            {(
              [
                ["Queued", a.t_queued],
                ["Launch", a.t_launch],
                ["First event", a.t_first_event],
                ["Exit", a.t_exit],
                ["Validated", a.t_validated],
              ] as const
            ).map(([label, at]) => (
              <li key={label}>
                {label}: {at ?? "pending"}
              </li>
            ))}
          </ol>
          {a.error_detail && (
            <p className="mt-2 text-danger">
              {a.error_class}: {a.error_detail}
            </p>
          )}
        </li>
      ))}
      {!attempts.length && <li>No attempts yet.</li>}
    </ol>
  );
}
