import * as React from "react";
import { cn } from "@/ui/utils";
import { fieldControlClass, fieldFocusClass } from "@/ui/primitives/field-control";

const CHEVRON =
  "url(\"data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%238b909c' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")";

const Select = React.forwardRef<
  HTMLSelectElement,
  React.ComponentProps<"select"> & { fieldSize?: "default" | "sm" }
>(({ className, style, fieldSize = "default", children, ...props }, ref) => (
  <select
    ref={ref}
    className={cn(
      "flex w-full appearance-none disabled:cursor-not-allowed disabled:opacity-50",
      fieldControlClass,
      fieldFocusClass,
      fieldSize === "sm" ? "h-8 py-1 pl-2 pr-8 text-xs" : "h-10 py-2 pl-3 pr-10 text-sm",
      className,
    )}
    style={{
      backgroundImage: CHEVRON,
      backgroundRepeat: "no-repeat",
      backgroundPosition: fieldSize === "sm" ? "right 0.5rem center" : "right 0.75rem center",
      backgroundSize: fieldSize === "sm" ? "0.875rem" : "1rem",
      ...style,
    }}
    {...props}
  >
    {children}
  </select>
));
Select.displayName = "Select";

export { Select };
