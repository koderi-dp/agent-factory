import type { Box } from "./layout";
export function GroupFrame({ group }: { group: Box & { label: string } }) {
  return (
    <div
      className="absolute rounded-lg bg-inset p-2 text-xs text-muted-foreground shadow-[var(--shadow-border)]"
      style={{ left: group.x, top: group.y, width: group.w, height: group.h }}
    >
      {group.label}
    </div>
  );
}
