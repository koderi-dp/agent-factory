import type { ReactNode } from "react";
import { cn } from "@/ui/utils";

export function PageShell({
  children,
  className,
  gap = "default",
}: {
  children: ReactNode;
  className?: string;
  /** `loose` for dashboard-style pages with more vertical rhythm. */
  gap?: "default" | "loose";
}) {
  return (
    <div
      className={cn(
        "flex w-full min-w-0 flex-col px-4 py-6 lg:px-6 lg:py-8",
        gap === "loose" ? "gap-8" : "gap-6",
        className,
      )}
    >
      {children}
    </div>
  );
}
