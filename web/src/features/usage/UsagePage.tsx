import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, keys, type UsageMetric, type UsageRow } from "@/lib/api";
import { tokens } from "@/lib/format";
import { Button } from "@/ui/primitives/button";
import { Select } from "@/ui/primitives/select";
import { PageHeader } from "@/ui/page-header";
import { PageShell } from "@/ui/page-shell";

const RANGES = [7, 30, 90];

type Group = {
  attempts: number;
  input: UsageMetric;
  cached_input: UsageMetric;
  output: UsageMetric;
  cost_usd: UsageMetric;
};

function merge(a: UsageMetric, b: UsageMetric): UsageMetric {
  return {
    value: a.value == null ? b.value : b.value == null ? a.value : a.value + b.value,
    unknown: a.unknown + b.unknown,
  };
}

function group(rows: readonly UsageRow[], keyFn: (row: UsageRow) => string) {
  const groups = new Map<string, Group>();
  for (const row of rows) {
    const key = keyFn(row);
    const current = groups.get(key);
    groups.set(
      key,
      current
        ? {
            attempts: current.attempts + row.attempts,
            input: merge(current.input, row.input),
            cached_input: merge(current.cached_input, row.cached_input),
            output: merge(current.output, row.output),
            cost_usd: merge(current.cost_usd, row.cost_usd),
          }
        : {
            attempts: row.attempts,
            input: row.input,
            cached_input: row.cached_input,
            output: row.output,
            cost_usd: row.cost_usd,
          },
    );
  }
  return [...groups].sort(([a], [b]) => a.localeCompare(b));
}

function TokenCell({ metric }: { metric: UsageMetric }) {
  return (
    <td className="px-3 py-2 text-right tabular-nums">
      {metric.value == null ? "unknown" : tokens(metric.value)}
      {metric.value != null && metric.unknown > 0 ? ` · ${metric.unknown} unknown` : ""}
    </td>
  );
}

function CostCell({ metric }: { metric: UsageMetric }) {
  return (
    <td className="px-3 py-2 text-right tabular-nums">
      {metric.value == null ? "not reported" : `$${metric.value.toFixed(4)} est.`}
      {metric.value != null && metric.unknown > 0 ? ` · ${metric.unknown} not reported` : ""}
    </td>
  );
}

function UsageTable({
  label,
  heading,
  groups,
  name = (key) => key,
}: {
  label: string;
  heading: string;
  groups: [string, Group][];
  name?: (key: string) => string;
}) {
  return (
    <section className="space-y-2" aria-label={label}>
      <h2 className="text-sm font-medium">{label}</h2>
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-sm">
          <thead className="text-xs text-muted-foreground">
            <tr className="border-b border-border">
              <th scope="col" className="px-3 py-2 text-left font-medium">
                {heading}
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Attempts
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Input
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Cached input
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Output
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Est. cost
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {groups.map(([key, g]) => (
              <tr key={key}>
                <th scope="row" className="px-3 py-2 text-left font-medium">
                  {name(key)}
                </th>
                <td className="px-3 py-2 text-right tabular-nums">{g.attempts}</td>
                <TokenCell metric={g.input} />
                <TokenCell metric={g.cached_input} />
                <TokenCell metric={g.output} />
                <CostCell metric={g.cost_usd} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function UsagePage() {
  const [days, setDays] = useState(30);
  const query = useQuery({ queryKey: keys.usage(days), queryFn: () => api.usage(days) });
  const rows = query.data?.rows ?? [];
  const unavailable = (query.data?.repos ?? []).filter((r) => r.status !== "available");
  const perDay = group(rows, (row) => row.day);
  const chart = perDay.map(([day, g]) => ({
    day,
    input: g.input.value ?? undefined,
    cached_input: g.cached_input.value ?? undefined,
    output: g.output.value ?? undefined,
  }));
  return (
    <PageShell>
      <PageHeader
        title="Usage and costs"
        description="Token usage per day, provider and repository. Costs are estimates reported by providers, not billing; days are UTC."
        actions={
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            Range
            <Select value={String(days)} onChange={(e) => setDays(Number(e.target.value))}>
              {RANGES.map((n) => (
                <option key={n} value={n}>
                  Last {n} days
                </option>
              ))}
            </Select>
          </label>
        }
      />
      {unavailable.map((r) => (
        <p key={r.id} className="text-sm text-warn-fg">
          {r.name}:{" "}
          {r.detail ??
            (r.status === "missing"
              ? "Repository missing"
              : r.status === "version_mismatch"
                ? "Upgrade required"
                : "Unreadable")}
        </p>
      ))}
      {query.isPending && (
        <p role="status" className="text-sm text-muted-foreground">
          Loading usage…
        </p>
      )}
      {query.isError && (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <p role="alert">Could not load usage: {query.error.message}</p>
          <Button variant="secondary" onClick={() => void query.refetch()}>
            Retry
          </Button>
        </div>
      )}
      {query.data && rows.length === 0 && (
        <p className="text-sm text-muted-foreground">No attempts in this period.</p>
      )}
      {rows.length > 0 && (
        <>
          <section aria-label="Tokens per day" className="h-64 rounded-lg border border-border p-3">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chart}>
                <CartesianGrid stroke="var(--color-border)" vertical={false} />
                <XAxis
                  dataKey="day"
                  tick={{ fill: "var(--color-muted-foreground)", fontSize: 11 }}
                />
                <YAxis
                  tickFormatter={(v: number) => tokens(v)}
                  tick={{ fill: "var(--color-muted-foreground)", fontSize: 11 }}
                />
                <Tooltip
                  formatter={(v: number) => tokens(v)}
                  contentStyle={{
                    background: "var(--color-popover)",
                    border: "1px solid var(--color-border)",
                    color: "var(--color-popover-foreground)",
                  }}
                />
                <Legend />
                <Bar dataKey="input" name="Input" stackId="t" fill="var(--color-signal)" />
                <Bar
                  dataKey="cached_input"
                  name="Cached input"
                  stackId="t"
                  fill="var(--color-warn)"
                />
                <Bar dataKey="output" name="Output" stackId="t" fill="var(--color-term-cyan)" />
              </BarChart>
            </ResponsiveContainer>
          </section>
          <UsageTable label="Per day" heading="Day" groups={perDay} />
          <UsageTable
            label="Per provider"
            heading="Provider"
            groups={group(rows, (row) => row.provider)}
          />
          <UsageTable
            label="Per repository"
            heading="Repository"
            groups={group(rows, (row) => row.repo_id)}
            name={(id) => rows.find((row) => row.repo_id === id)?.repo_name ?? id}
          />
        </>
      )}
    </PageShell>
  );
}
