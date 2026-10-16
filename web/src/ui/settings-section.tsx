import { Save, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/ui/primitives/button";
import { cn } from "@/ui/utils";

export type SettingsSectionDefinition<Id extends string = string> = {
  id: Id;
  label: string;
  description: string;
  icon: LucideIcon;
};

/** Pylon's settings layout: a section list with descriptions beside the form. */
export function SettingsSectionLayout<Id extends string>({
  label,
  sections,
  activeSection,
  onSectionChange,
  children,
}: {
  label: string;
  sections: readonly SettingsSectionDefinition<Id>[];
  activeSection: Id;
  onSectionChange: (section: Id) => void;
  children: ReactNode;
}) {
  const section = sections.find((item) => item.id === activeSection) ?? sections[0];
  if (!section) return null;

  return (
    <div className="flex min-h-0 flex-1 flex-col md:flex-row">
      <nav
        aria-label={label}
        className="shrink-0 border-b border-border md:w-52 md:border-r md:border-b-0"
      >
        <div className="flex gap-1 overflow-x-auto p-2 md:grid md:overflow-visible md:p-3">
          {sections.map((item) => {
            const Icon = item.icon;
            const active = item.id === section.id;
            return (
              <button
                key={item.id}
                type="button"
                aria-current={active ? "page" : undefined}
                onClick={() => onSectionChange(item.id)}
                className={cn(
                  "flex shrink-0 items-center gap-2.5 rounded-md px-3 py-2 text-left transition-colors md:w-full",
                  active
                    ? "bg-secondary text-foreground shadow-[var(--shadow-border)]"
                    : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
                )}
              >
                <Icon className="size-4 shrink-0" />
                <span className="grid gap-0.5">
                  <span className="text-sm font-medium">{item.label}</span>
                  <span className="hidden text-[11px] leading-tight text-muted-foreground md:block">
                    {item.description}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </nav>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="grid w-full max-w-2xl content-start gap-4 px-4 py-4 md:px-6 md:py-5">
          <div className="border-b border-border pb-3">
            <h3 className="text-base font-semibold">{section.label}</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">{section.description}</p>
          </div>
          {children}
        </div>
      </div>
    </div>
  );
}

/** Sticky footer of a settings form. Says whether there is anything to save. */
export function SettingsSaveFooter({
  dirty,
  isSaving,
  onCancel,
  submitLabel = "Save",
  note,
}: {
  dirty: boolean;
  isSaving: boolean;
  onCancel?: () => void;
  submitLabel?: string;
  /** What saving will do beyond storing the change, such as restarting the source. */
  note?: string;
}) {
  return (
    <div className="shrink-0 border-t border-border bg-background/95 px-4 py-3 backdrop-blur">
      <div className="flex items-center justify-between gap-3">
        <span className="min-w-0 text-xs text-muted-foreground" aria-live="polite">
          {isSaving ? "Saving changes…" : dirty ? (note ?? "Unsaved changes") : "All changes saved"}
        </span>
        <div className="flex shrink-0 items-center gap-2">
          {onCancel && dirty ? (
            <Button size="sm" type="button" variant="ghost" disabled={isSaving} onClick={onCancel}>
              Discard
            </Button>
          ) : null}
          <Button icon={Save} loading={isSaving} size="sm" type="submit" disabled={!dirty}>
            {submitLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
