/** Shared focus for inset fields — subtle border lift, not a bright ring. */
export const fieldFocusClass =
  "focus:outline-none focus-visible:outline-none focus:shadow-[var(--shadow-border-hover)] focus-visible:shadow-[var(--shadow-border-hover)]";

export const fieldControlClass =
  "rounded-md bg-inset text-foreground shadow-[var(--shadow-border)]";

/** Filter/toolbar rows — flat until focus, then same subtle lift as inset fields. */
export const embeddedFieldClass = "border-0 bg-secondary/50 shadow-none";

export const embeddedFieldFocusClass = fieldFocusClass;
