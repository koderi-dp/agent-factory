import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/ui/utils";
import { LoaderCircle, type LucideIcon } from "lucide-react";

const buttonVariants = cva(
  "relative inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-[color,background-color,box-shadow,opacity] duration-150 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-40 aria-disabled:pointer-events-none aria-disabled:opacity-40 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        secondary:
          "bg-secondary text-secondary-foreground shadow-[var(--shadow-border)] hover:shadow-[var(--shadow-border-hover)]",
        outline: "bg-transparent text-foreground shadow-[var(--shadow-border)] hover:bg-secondary",
        ghost: "text-muted-foreground hover:bg-secondary hover:text-foreground",
        destructive: "bg-destructive text-background hover:bg-destructive hover:underline",
        link: "text-foreground underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-3",
        sm: "h-8 px-2.5 text-xs",
        lg: "h-10 px-4",
        icon: "size-9 p-0",
        "icon-sm": "size-8 p-0",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  /** Prefer icon + text for actions. Keep labels stable while loading. */
  icon?: LucideIcon;
  loading?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      className,
      variant,
      size,
      asChild = false,
      icon: Icon,
      loading = false,
      disabled,
      children,
      ...props
    },
    ref,
  ) => {
    const Comp = asChild ? Slot : "button";
    // Slot must receive the caller's single child, without a wrapper.
    const content = asChild ? (
      children
    ) : (
      <>
        {Icon &&
          (loading ? (
            <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
          ) : (
            <Icon aria-hidden="true" />
          ))}
        {!Icon && loading ? (
          <>
            <span className="inline-flex items-center justify-center gap-2 opacity-0">
              {children}
            </span>
            <LoaderCircle
              aria-hidden="true"
              className="absolute animate-spin motion-reduce:animate-none"
            />
          </>
        ) : (
          children
        )}
      </>
    );
    return (
      <Comp
        className={cn(
          buttonVariants({ variant, size, className }),
          loading && "disabled:!opacity-100",
        )}
        ref={ref}
        {...props}
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        aria-disabled={disabled || loading || undefined}
        tabIndex={asChild && (disabled || loading) ? -1 : props.tabIndex}
        onClickCapture={(event) => {
          if (disabled || loading) {
            event.preventDefault();
            event.stopPropagation();
            return;
          }
          props.onClickCapture?.(event);
        }}
      >
        {content}
      </Comp>
    );
  },
);
Button.displayName = "Button";

/** Compact toolbar action with a required accessible name and native tooltip. */
const IconButton = React.forwardRef<
  HTMLButtonElement,
  Omit<ButtonProps, "children" | "asChild" | "size" | "icon"> & {
    icon: LucideIcon;
    label: string;
    size?: "icon" | "icon-sm";
  }
>(({ label, size = "icon-sm", variant = "ghost", ...props }, ref) => (
  <Button {...props} ref={ref} size={size} variant={variant} aria-label={label} title={label} />
));
IconButton.displayName = "IconButton";

export { Button, IconButton };
