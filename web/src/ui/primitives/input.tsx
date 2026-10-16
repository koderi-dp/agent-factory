import * as React from "react";
import { cn } from "@/ui/utils";
import { fieldControlClass, fieldFocusClass } from "@/ui/primitives/field-control";

const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<"input">>(
  ({ className, type, ...props }, ref) => (
    <input
      type={type}
      className={cn(
        "flex h-10 w-full px-3 text-sm placeholder:text-muted-foreground/70 disabled:cursor-not-allowed disabled:opacity-50",
        fieldControlClass,
        fieldFocusClass,
        className,
      )}
      ref={ref}
      {...props}
    />
  ),
);
Input.displayName = "Input";

export { Input };
