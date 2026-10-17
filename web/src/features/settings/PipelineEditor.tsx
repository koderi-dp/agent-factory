import { useId, type ReactNode } from "react";
import { ArrowDown, ArrowUp, Group, Plus, Trash2, Ungroup } from "lucide-react";
import type { PipelinePreview } from "@/lib/api";
import { Badge } from "@/ui/primitives/badge";
import { Button } from "@/ui/primitives/button";
import { Card } from "@/ui/primitives/card";
import { Input } from "@/ui/primitives/input";
import { Select } from "@/ui/primitives/select";
import { cn } from "@/ui/utils";
import {
  MAX_GROUP_MEMBERS,
  MAX_STAGES,
  addGroupMember,
  addStage,
  editExecution,
  entries,
  errorsAt,
  errorsUnder,
  groupWithNext,
  isGroup,
  moveStage,
  removeGroupMember,
  removeStage,
  setStageField,
  stageKinds,
  ungroup,
  type Config,
  type Entry,
  type FieldError,
} from "./model";

type Role = { name: string; provider: string; type: string };
type Props = {
  config: Config;
  name: string;
  errors: FieldError[];
  preview?: PipelinePreview;
  roles: Role[];
  onChange: (next: Config) => void;
};

function Messages({ id, messages }: { id: string; messages: string[] }) {
  if (!messages.length) return null;
  return (
    <span id={id} className="block text-xs text-danger">
      {messages.map((message, i) => (
        <span key={i} className="block">
          {message}
        </span>
      ))}
    </span>
  );
}

function Field({
  label,
  messages,
  children,
}: {
  label: string;
  messages: string[];
  children: (props: { "aria-describedby"?: string; "aria-invalid"?: boolean }) => ReactNode;
}) {
  const errorId = useId();
  const invalid = messages.length > 0;
  return (
    <div className="grid min-w-0 gap-1 text-xs text-muted-foreground">
      <span>{label}</span>
      {children({
        "aria-describedby": invalid ? errorId : undefined,
        "aria-invalid": invalid || undefined,
      })}
      <Messages id={errorId} messages={messages} />
    </div>
  );
}

export function PipelineEditor({ config, name, errors, preview, roles, onChange }: Props) {
  const steps: Entry[] = config.pipelines[name] ?? [];
  const kinds = stageKinds(steps, preview, roles);
  const listId = useId();
  const base = (i: number) => `pipelines.${name}.${i}`;
  const stageCount = steps.length;
  const add = (at: number) =>
    stageCount < MAX_STAGES && (
      <Button
        type="button"
        variant="outline"
        size="sm"
        icon={Plus}
        onClick={() => onChange(addStage(config, name, at))}
      >
        Add stage
      </Button>
    );
  return (
    <div className="grid gap-3">
      <datalist id={`${listId}-roles`}>
        {[...new Set(roles.map((role) => role.name))].map((role) => (
          <option key={role} value={role} />
        ))}
      </datalist>
      <ol aria-label="Pipeline stages" className="grid gap-3">
        {steps.map((step, i) => {
          const group = isGroup(step);
          const kind = kinds[i];
          const path = base(i);
          const stageErrors = [...errorsAt(errors, path), ...errorsAt(errors, `${path}.parallel`)];
          const invalid = errorsUnder(errors, path).length > 0;
          const targets = steps.slice(0, i).filter((_, k) => kinds[k] === "task");
          const nextStep = steps[i + 1];
          const canGroup =
            kind === "review" &&
            !group &&
            !!nextStep &&
            !isGroup(nextStep) &&
            kinds[i + 1] === "review";
          const canChanges = kind === "review" || kind === "approval";
          return (
            <li key={i}>
              <Card className={cn("grid gap-3 p-4", invalid && "ring-1 ring-danger")}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">Stage {i + 1}</span>
                  {kind && <Badge variant="outline">{kind}</Badge>}
                  {group && <Badge>parallel group</Badge>}
                  <span className="ml-auto flex flex-wrap gap-1">
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Move stage ${i + 1} up`}
                      disabled={i === 0}
                      onClick={() => onChange(moveStage(config, name, i, -1))}
                    >
                      <ArrowUp />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Move stage ${i + 1} down`}
                      disabled={i === stageCount - 1}
                      onClick={() => onChange(moveStage(config, name, i, 1))}
                    >
                      <ArrowDown />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Remove stage ${i + 1}`}
                      disabled={stageCount <= 1}
                      onClick={() => onChange(removeStage(config, name, i))}
                    >
                      <Trash2 />
                    </Button>
                  </span>
                </div>
                <Messages id={`${listId}-${i}-stage`} messages={stageErrors} />
                <div className="grid gap-3 sm:grid-cols-3">
                  <Field label="Stage id" messages={errorsAt(errors, `${path}.id`)}>
                    {(a11y) => (
                      <Input
                        {...a11y}
                        aria-label={`Stage ${i + 1} id`}
                        value={step.id}
                        onChange={(e) =>
                          onChange(setStageField(config, name, i, "id", e.target.value))
                        }
                      />
                    )}
                  </Field>
                  <Field label="Label" messages={errorsAt(errors, `${path}.label`)}>
                    {(a11y) => (
                      <Input
                        {...a11y}
                        aria-label={`Stage ${i + 1} label`}
                        value={step.label ?? ""}
                        onChange={(e) =>
                          onChange(setStageField(config, name, i, "label", e.target.value))
                        }
                      />
                    )}
                  </Field>
                  {canChanges && (
                    <Field label="On changes" messages={errorsAt(errors, `${path}.onChanges`)}>
                      {(a11y) => (
                        <Select
                          {...a11y}
                          aria-label={`Stage ${i + 1} on changes`}
                          value={step.onChanges ?? ""}
                          onChange={(e) =>
                            onChange(setStageField(config, name, i, "onChanges", e.target.value))
                          }
                        >
                          <option value="">none</option>
                          {step.onChanges && !targets.some((t) => t.id === step.onChanges) && (
                            <option value={step.onChanges}>{step.onChanges}</option>
                          )}
                          {targets.map((target) => (
                            <option key={target.id} value={target.id}>
                              {target.id}
                            </option>
                          ))}
                        </Select>
                      )}
                    </Field>
                  )}
                </div>
                {entries(step).map((entry, j) => {
                  const entryPath = group ? `${path}.parallel.${j}` : path;
                  const profile = entry.profiles[entry.provider] ?? { model: "", effort: "" };
                  const profilePath = `${entryPath}.profiles.${entry.provider}`;
                  const change = (field: "uses" | "provider" | "model" | "effort", value: string) =>
                    onChange(editExecution(config, name, i, j, field, value));
                  return (
                    <div
                      key={j}
                      className={cn(
                        "grid gap-3 sm:grid-cols-2 lg:grid-cols-4",
                        group && "rounded-md bg-secondary/40 p-3",
                      )}
                    >
                      {group && (
                        <Field label="Execution id" messages={errorsAt(errors, `${entryPath}.id`)}>
                          {(a11y) => (
                            <Input
                              {...a11y}
                              aria-label={`${entry.id} id`}
                              value={entry.id}
                              onChange={(e) => {
                                const next = structuredClone(config);
                                const target = next.pipelines[name][i];
                                if (isGroup(target)) target.parallel[j].id = e.target.value;
                                onChange(next);
                              }}
                            />
                          )}
                        </Field>
                      )}
                      <Field label="Role" messages={errorsAt(errors, `${entryPath}.uses`)}>
                        {(a11y) => (
                          <Input
                            {...a11y}
                            aria-label={`${entry.id} role`}
                            list={`${listId}-roles`}
                            value={entry.uses}
                            onChange={(e) => change("uses", e.target.value)}
                          />
                        )}
                      </Field>
                      <Field
                        label="Provider"
                        messages={[
                          ...errorsAt(errors, `${entryPath}.provider`),
                          ...errorsAt(errors, profilePath),
                        ]}
                      >
                        {(a11y) => (
                          <Select
                            {...a11y}
                            aria-label={`${entry.id} provider`}
                            value={entry.provider}
                            onChange={(e) => change("provider", e.target.value)}
                          >
                            <option>codex</option>
                            <option>claude</option>
                            <option>opencode</option>
                          </Select>
                        )}
                      </Field>
                      <Field label="Model" messages={errorsAt(errors, `${profilePath}.model`)}>
                        {(a11y) => (
                          <Input
                            {...a11y}
                            aria-label={`${entry.id} model`}
                            value={profile.model}
                            onChange={(e) => change("model", e.target.value)}
                          />
                        )}
                      </Field>
                      <Field label="Effort" messages={errorsAt(errors, `${profilePath}.effort`)}>
                        {(a11y) => (
                          <Input
                            {...a11y}
                            aria-label={`${entry.id} effort`}
                            value={profile.effort}
                            onChange={(e) => change("effort", e.target.value)}
                          />
                        )}
                      </Field>
                      {group && isGroup(step) && step.parallel.length > 1 && (
                        <div className="flex items-end">
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            icon={Trash2}
                            aria-label={`Remove ${entry.id}`}
                            onClick={() => onChange(removeGroupMember(config, name, i, j))}
                          >
                            Remove
                          </Button>
                        </div>
                      )}
                    </div>
                  );
                })}
                <div className="flex flex-wrap gap-2">
                  {canGroup && (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      icon={Group}
                      onClick={() => onChange(groupWithNext(config, name, i))}
                    >
                      Group with next review
                    </Button>
                  )}
                  {group && isGroup(step) && step.parallel.length < MAX_GROUP_MEMBERS && (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      icon={Plus}
                      onClick={() => onChange(addGroupMember(config, name, i))}
                    >
                      Add review
                    </Button>
                  )}
                  {group && (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      icon={Ungroup}
                      onClick={() => onChange(ungroup(config, name, i))}
                    >
                      Ungroup
                    </Button>
                  )}
                  {add(i)}
                </div>
              </Card>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
