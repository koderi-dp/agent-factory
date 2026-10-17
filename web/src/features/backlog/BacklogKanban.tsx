import { useState } from "react";
import type { BacklogPriority, BacklogStatus } from "@/lib/api";
import { Badge } from "@/ui/primitives/badge";
import { Select } from "@/ui/primitives/select";
import {
  backlogPriorities,
  backlogStatuses,
  kanbanColumns,
  priorityLabel,
  statusLabel,
  type BacklogItem,
  type BacklogTarget,
} from "./model";

export function BacklogKanban({
  backlog,
  busy,
  onMove,
}: {
  backlog: BacklogItem[];
  busy: boolean;
  onMove: (item: BacklogItem, target: BacklogTarget) => void;
}) {
  const [drag, setDrag] = useState<string>();
  return (
    <div
      className="flex gap-4 overflow-x-auto pb-3"
      tabIndex={0}
      aria-label="Backlog board columns"
    >
      {kanbanColumns(backlog).map((column) => (
        <section
          key={column.status}
          aria-label={column.label}
          className="w-80 shrink-0 space-y-3 rounded-lg bg-inset p-3"
        >
          <h3 className="flex items-center justify-between text-sm font-medium">
            {column.label}
            <Badge>{column.count}</Badge>
          </h3>
          {column.groups.map((group) => (
            <div
              key={group.priority}
              role="group"
              aria-label={`${column.label}, ${group.label} priority`}
              className="space-y-2 rounded-md p-1"
              onDragOver={(e) => {
                if (drag && !busy) e.preventDefault();
              }}
              onDrop={(e) => {
                e.preventDefault();
                const item = backlog.find((candidate) => candidate.key === drag);
                setDrag(undefined);
                if (item && !busy)
                  onMove(item, { status: column.status, priority: group.priority });
              }}
            >
              <h4 className="text-xs font-medium text-muted-foreground">{group.label}</h4>
              {group.items.map((item) => (
                <article
                  key={item.key}
                  draggable={!busy}
                  onDragStart={(e) => {
                    setDrag(item.key);
                    e.dataTransfer.setData("text/plain", item.key);
                  }}
                  onDragEnd={() => setDrag(undefined)}
                  className="space-y-2 rounded-md bg-card p-3 shadow-[var(--shadow-border)]"
                >
                  <h5 className="text-sm font-medium">{item.title}</h5>
                  <p className="line-clamp-3 whitespace-pre-wrap text-xs text-muted-foreground">
                    {item.details}
                  </p>
                  <Badge variant="outline">{priorityLabel(item.priority)}</Badge>
                  <div className="grid gap-2">
                    <Select
                      fieldSize="sm"
                      aria-label={`Status of ${item.title}`}
                      disabled={busy}
                      value={item.status}
                      onChange={(e) =>
                        onMove(item, {
                          status: e.target.value as BacklogStatus,
                          priority: item.priority,
                        })
                      }
                    >
                      {backlogStatuses.map((status) => (
                        <option key={status} value={status}>
                          {statusLabel(status)}
                        </option>
                      ))}
                    </Select>
                    <Select
                      fieldSize="sm"
                      aria-label={`Priority of ${item.title}`}
                      disabled={busy}
                      value={item.priority}
                      onChange={(e) =>
                        onMove(item, {
                          status: item.status,
                          priority: e.target.value as BacklogPriority,
                        })
                      }
                    >
                      {backlogPriorities.map((priority) => (
                        <option key={priority} value={priority}>
                          {priorityLabel(priority)}
                        </option>
                      ))}
                    </Select>
                  </div>
                </article>
              ))}
              {!group.items.length && (
                <p className="py-2 text-xs text-muted-foreground">No items</p>
              )}
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
