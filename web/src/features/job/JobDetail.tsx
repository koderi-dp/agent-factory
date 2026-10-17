import { useCallback, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocation, useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import { api, keys, report } from "@/lib/api";
import {
  frozenSteps,
  currentExecutions,
  latestAttempt,
  jobActions,
  needsSensitiveConfirm,
  type Action,
  type Execution,
} from "@/features/board/model";
import { interventionFor } from "./intervention";
import { layout } from "./pipeline/layout";
import { PipelineView } from "./pipeline/PipelineView";
import { ChangesView } from "./ChangesView";
import { ReportView } from "./ReportView";
import { LogView } from "./LogView";
import { Timeline } from "./Timeline";
import { UsagePanel } from "./UsagePanel";
import { InterventionPanel } from "./PauseBanner";
import { ReviewerCards } from "./ReviewerCards";
import { PageShell } from "@/ui/page-shell";
import { JobHeader } from "./JobHeader";
import { StageDetails } from "./StageDetails";
import { Button } from "@/ui/primitives/button";
import { Input } from "@/ui/primitives/input";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/ui/primitives/tabs";
import { Sheet, SheetContent } from "@/ui/primitives/sheet";
import { DialogTitle, DialogDescription } from "@/ui/primitives/dialog";
import { ConfirmDialog } from "@/ui/confirm-dialog";

function ExecutionReport({
  repo,
  job,
  execution,
  file,
}: {
  repo: string;
  job: string;
  execution?: Execution;
  file?: string;
}) {
  const path = file || execution?.report_path;
  const query = useQuery({
    queryKey: ["repo", repo, "report", job, path],
    queryFn: () => report(repo, job, path!),
    enabled: !!path,
  });
  if (!path) return <p>No report yet.</p>;
  if (query.isPending) return <p role="status">Loading report…</p>;
  if (query.isError) return <p role="alert">Could not load report: {query.error.message}</p>;
  return <ReportView text={query.data} />;
}
const tabNames = ["Pipeline", "Changes", "Reviews", "Reports", "Timeline", "Log", "Usage"];
const labels: Record<Action, string> = {
  stop: "Stop",
  resume: "Resume",
  approve: "Merge",
  rework: "Rework",
  delete: "Delete",
};
export function JobDetail({
  repo,
  job: jobId,
  name,
}: {
  repo: string;
  job: string;
  name?: string;
}) {
  const query = useQuery({ queryKey: keys.job(repo, jobId), queryFn: () => api.job(repo, jobId) });
  const state = useQuery({ queryKey: keys.state(repo), queryFn: () => api.state(repo) });
  const location = useLocation(),
    navigate = useNavigate();
  const search = new URLSearchParams(location.searchStr),
    nodeId = search.get("node"),
    file = search.get("file");
  const [action, setAction] = useState<Action>(),
    [pending, setPending] = useState(false),
    [error, setError] = useState("");
  const [note, setNote] = useState(""),
    [sensitive, setSensitive] = useState(false),
    [force, setForce] = useState(false),
    [history, setHistory] = useState(false);
  const [selectedExec, setSelectedExec] = useState<number>();
  const selectNode = useCallback(
    (node?: string) => {
      void navigate({ to: location.pathname, search: node ? { node } : {}, replace: false });
    },
    [navigate, location.pathname],
  );
  if (query.isPending)
    return (
      <PageShell>
        <p role="status">Loading job…</p>
      </PageShell>
    );
  if (query.isError)
    return (
      <PageShell>
        <p role="alert">Could not load job: {query.error.message}</p>
      </PageShell>
    );
  const { job, executions, attempts } = query.data,
    steps = frozenSteps(job);
  const current = currentExecutions(steps, executions);
  const node = layout(steps, executions).nodes.find((n) => n.id === nodeId);
  const execution =
    executions.find((e) => e.id === selectedExec) ??
    current.find((e) => e.state === "running") ??
    current.at(-1);
  const historyExecution = nodeId?.startsWith("history:")
    ? executions.find((e) => e.id === Number(nodeId.slice(8)))
    : undefined;
  const selected = node?.execution ?? historyExecution;
  const selectedAttempts = attempts.filter((a) => a.execution_id === selected?.id);
  const actions = jobActions(job, executions);
  const stageStep = steps.find((s) => s.id === (node?.step ?? selected?.step_id));
  const stageEntry = node
    ? stageStep?.executions[node.execIndex]
    : stageStep?.executions.find((e) => e.id === selected?.exec_id);
  const needsYou = job.state === "paused" || job.state === "needs_human";
  const startAction = (a: Action) => {
    setAction(a);
    setError("");
    setSensitive(false);
    setForce(false);
    setNote("");
  };
  const sensitiveRequired = action === "approve" && needsSensitiveConfirm(job);
  const sensitivePaths = sensitiveRequired
    ? (interventionFor(job, steps, current)?.sensitivePaths ?? [])
    : [];
  const confirm = async () => {
    if (!action) return;
    setPending(true);
    setError("");
    try {
      await api.action(repo, jobId, action, { note, confirm_sensitive: sensitive, force });
      toast.success(`${labels[action]} requested`);
      setAction(undefined);
      if (action === "delete") void navigate({ to: `/repo/${encodeURIComponent(repo)}` });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  };
  const toSource = (e: (typeof executions)[number]) => {
    const attempt = latestAttempt(e, attempts);
    return attempt
      ? [
          {
            attempt: attempt.id,
            stage: e.step_id,
            agent: e.exec_id,
            live: ["launching", "running"].includes(attempt.state),
          },
        ]
      : [];
  };
  const logSources = (history ? executions : current).flatMap(toSource);
  const nodeAttempt = selected && latestAttempt(selected, attempts);
  const nodeSources = selected ? toSource(selected) : [];
  const executionPicker = (
    <label className="block text-sm">
      Execution{" "}
      <select
        className="ml-2 rounded bg-secondary p-2"
        value={execution?.id ?? ""}
        onChange={(e) => setSelectedExec(Number(e.target.value))}
      >
        {(history ? executions : current).map((e) => (
          <option key={e.id} value={e.id}>
            {e.step_id}/{e.exec_id} · round {e.round} · {e.state}
          </option>
        ))}
      </select>
    </label>
  );
  return (
    <PageShell gap="loose">
      <JobHeader
        job={job}
        attempts={attempts}
        repo={repo}
        name={name}
        actions={(needsYou ? actions.filter((a) => a === "delete") : actions).map((a) => (
          <Button
            key={a}
            variant={a === "delete" ? "destructive" : "outline"}
            onClick={() => startAction(a)}
          >
            {labels[a]}
          </Button>
        ))}
      />
      <InterventionPanel
        job={job}
        steps={steps}
        executions={current}
        actions={actions}
        onAction={startAction}
        onOpenReport={selectNode}
      />
      <Tabs defaultValue={file ? "Reports" : "Pipeline"}>
        <TabsList aria-label="Job views">
          {tabNames.map((t) => (
            <TabsTrigger key={t} value={t}>
              {t}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="Pipeline">
          <PipelineView
            steps={steps}
            executions={executions}
            attempts={attempts}
            activity={state.data?.activity}
            paused={job.state === "paused" ? (job.pause_reason ?? "Paused") : undefined}
            onOpen={selectNode}
          />
          <label className="text-sm">
            <input
              type="checkbox"
              checked={history}
              onChange={(e) => setHistory(e.target.checked)}
            />{" "}
            Show history
          </label>
          {history && (
            <ul className="mt-3 space-y-2">
              {executions
                .filter((e) => e.state === "superseded")
                .map((e) => (
                  <li key={e.id} className="text-muted-foreground">
                    <span className="line-through">
                      {e.step_id}/{e.exec_id} · {e.state} · round {e.round}
                    </span>
                    <Button
                      variant="ghost"
                      onClick={() => {
                        setSelectedExec(e.id);
                        selectNode(`history:${e.id}`);
                      }}
                    >
                      Inspect
                    </Button>
                  </li>
                ))}
            </ul>
          )}
        </TabsContent>
        <TabsContent value="Changes">
          <ChangesView repo={repo} job={job} executions={executions} />
        </TabsContent>
        <TabsContent value="Reviews">
          <ReviewerCards steps={steps} executions={executions} onOpenReport={selectNode} />
        </TabsContent>
        <TabsContent value="Reports">
          <div className="space-y-4">
            {executionPicker}
            <ExecutionReport
              repo={repo}
              job={jobId}
              execution={execution}
              file={file ?? undefined}
            />
          </div>
        </TabsContent>
        <TabsContent value="Timeline">
          <Timeline attempts={attempts} />
        </TabsContent>
        <TabsContent value="Log">
          <div className="space-y-4">
            <LogView key={repo} repo={repo} sources={logSources} fileName={job.id} />
          </div>
        </TabsContent>
        <TabsContent value="Usage">
          <UsagePanel attempts={attempts} />
        </TabsContent>
      </Tabs>
      <Sheet
        open={!!nodeId}
        onOpenChange={(open) => {
          if (!open) selectNode();
        }}
      >
        <SheetContent side="right" className="w-full overflow-y-auto p-5 sm:w-[42rem]">
          <DialogTitle className="pr-8 text-lg">
            {selected ? `${selected.step_id}/${selected.exec_id}` : nodeId}
          </DialogTitle>
          <DialogDescription>
            Stage details, execution report, live log and attempt timeline
          </DialogDescription>
          <StageDetails
            step={stageStep}
            entry={stageEntry}
            execution={selected}
            attempts={attempts}
          />
          <Tabs
            key={nodeId}
            defaultValue={
              selected?.report_path ? "Report" : selected?.state === "running" ? "Log" : "Timeline"
            }
          >
            <TabsList>
              {["Report", "Log", "Timeline"].map((t) => (
                <TabsTrigger key={t} value={t}>
                  {t}
                </TabsTrigger>
              ))}
            </TabsList>
            <TabsContent value="Report">
              <ExecutionReport repo={repo} job={jobId} execution={selected} />
            </TabsContent>
            <TabsContent value="Log">
              <LogView
                key={`${repo}:${nodeAttempt?.id}`}
                repo={repo}
                sources={nodeSources}
                fileName={job.id}
              />
            </TabsContent>
            <TabsContent value="Timeline">
              <Timeline attempts={selectedAttempts} />
            </TabsContent>
          </Tabs>
        </SheetContent>
      </Sheet>
      <ConfirmDialog
        open={!!action}
        title={`${action ? labels[action] : "Confirm"} job?`}
        description={
          action === "delete"
            ? "Remove the job and its worktree. This cannot be undone."
            : action === "approve"
              ? "Merge this job into the repository's base branch."
              : action === "rework"
                ? "Return the job to its task step with your feedback."
                : "Apply this action to the job."
        }
        confirmLabel={action ? labels[action] : "Confirm"}
        destructive={action === "delete" || action === "stop"}
        pending={pending}
        confirmDisabled={(sensitiveRequired && !sensitive) || (action === "rework" && !note.trim())}
        onOpenChange={(open) => {
          if (!open && !pending) setAction(undefined);
        }}
        onConfirm={() => void confirm()}
      >
        {action === "rework" && (
          <label className="space-y-2 text-sm">
            Feedback
            <Input value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
        )}
        {action === "approve" && (
          <label className="text-sm">
            <input
              type="checkbox"
              checked={sensitive}
              onChange={(e) => setSensitive(e.target.checked)}
            />{" "}
            {sensitiveRequired ? (
              <>
                I reviewed the changes to these sensitive paths
                {sensitivePaths.length > 0 && <>: {sensitivePaths.join(", ")}</>}
              </>
            ) : (
              "Confirm changes to sensitive paths"
            )}
          </label>
        )}
        {action === "delete" && (
          <label className="text-sm">
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />{" "}
            Force deletion of an unmerged job
          </label>
        )}
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
      </ConfirmDialog>
    </PageShell>
  );
}
