import { Badge } from "@/ui/primitives/badge";
import { statusLabel, statusTone } from "@/ui/status-text";
import { cn } from "@/ui/utils";

export function StatusDot({ state, className }: { state: string; className?: string }) {
  const tone = statusTone(state);
  // Running and sending to nobody: the green of running, as a ring with nothing in it.
  const hollow = state === "not-sending";
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block shrink-0 rounded-full",
        hollow ? "size-2 border-[1.5px] border-online" : "size-1.5",
        tone === "live" && "bg-online",
        tone === "warn" && "bg-degraded",
        tone === "danger" && "bg-danger status-pulse",
        tone === "outline" && !hollow && "bg-offline",
        className,
      )}
    />
  );
}

/** Status is always text plus colour: the dot is decoration and the word is the name. */
export function StatusBadge({ state }: { state: string }) {
  return (
    <Badge variant={statusTone(state)} className="gap-1.5 whitespace-nowrap">
      <StatusDot state={state} />
      {statusLabel(state)}
    </Badge>
  );
}
