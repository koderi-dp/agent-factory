import { useBlocker } from "@tanstack/react-router";
import { ConfirmDialog } from "@/ui/confirm-dialog";
export function DirtyGuard({ dirty }: { dirty: boolean }) {
  const blocker = useBlocker({
    shouldBlockFn: () => dirty,
    enableBeforeUnload: dirty,
    withResolver: true,
  });
  return (
    <ConfirmDialog
      open={blocker.status === "blocked"}
      title="Discard unsaved changes?"
      description="Your settings edits have not been saved."
      confirmLabel="Discard and leave"
      destructive
      onOpenChange={(open) => {
        if (!open) blocker.reset?.();
      }}
      onConfirm={() => blocker.proceed?.()}
    />
  );
}
