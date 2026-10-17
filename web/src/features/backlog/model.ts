import type { BacklogPriority, BacklogStatus } from "@/lib/api";

export const backlogStatuses: readonly BacklogStatus[] = [
  "ready",
  "in_progress",
  "needs_input",
  "completed",
];
export const backlogPriorities: readonly BacklogPriority[] = ["high", "normal", "low"];

const statusLabels: Record<BacklogStatus, string> = {
  ready: "Ready",
  in_progress: "In progress",
  needs_input: "Needs input",
  completed: "Completed",
};
const priorityLabels: Record<BacklogPriority, string> = {
  high: "High",
  normal: "Normal",
  low: "Low",
};

export const statusLabel = (status: BacklogStatus) => statusLabels[status];
export const priorityLabel = (priority: BacklogPriority) => priorityLabels[priority];

export type BacklogItem = {
  index: number;
  key: string;
  title: string;
  details: string;
  priority: BacklogPriority;
  status: BacklogStatus;
};
export type BacklogTarget = { status: BacklogStatus; priority: BacklogPriority };
export type KanbanColumn = {
  status: BacklogStatus;
  label: string;
  count: number;
  groups: { priority: BacklogPriority; label: string; items: BacklogItem[] }[];
};

export function kanbanColumns(backlog: readonly BacklogItem[]): KanbanColumn[] {
  const ordered = [...backlog].sort((a, b) => a.index - b.index);
  return backlogStatuses.map((status) => ({
    status,
    label: statusLabel(status),
    count: ordered.filter((item) => item.status === status).length,
    groups: backlogPriorities.map((priority) => ({
      priority,
      label: priorityLabel(priority),
      items: ordered.filter((item) => item.status === status && item.priority === priority),
    })),
  }));
}

export function metaChange(
  item: Pick<BacklogItem, "key" | "status" | "priority">,
  target: BacklogTarget,
): { key: string; status?: BacklogStatus; priority?: BacklogPriority } | null {
  const status = target.status !== item.status;
  const priority = target.priority !== item.priority;
  if (!status && !priority) return null;
  return {
    key: item.key,
    ...(status ? { status: target.status } : {}),
    ...(priority ? { priority: target.priority } : {}),
  };
}
