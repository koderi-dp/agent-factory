import { z } from "zod";
import { stepSchema } from "@/features/board/model";
import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, settingsApi } from "@/lib/api";
import { PageShell } from "@/ui/page-shell";
import { Button } from "@/ui/primitives/button";
import { Input } from "@/ui/primitives/input";
import { Select } from "@/ui/primitives/select";
import { SettingsSaveFooter } from "@/ui/settings-section";
import { PipelineView } from "@/features/job/pipeline/PipelineView";
import { DirtyGuard } from "./DirtyGuard";
import {
  draftPipelineNames,
  draftState,
  editExecution,
  entries,
  fieldErrors,
  invalidSteps,
  parseDraft,
  previewDraft,
  type Config,
} from "./model";
const json = (value: unknown) => JSON.stringify(value, null, 2);

export function RepoSettings({ repo }: { repo: string }) {
  const state = useQuery({ queryKey: keys.state(repo), queryFn: () => api.state(repo) });
  const roles = useQuery({
    queryKey: ["roles", repo, state.data?.config],
    queryFn: () => settingsApi.roles(repo),
  });
  const [draft, setDraft] = useState<{ text: string; base: string }>();
  const [kept, setKept] = useState("");
  const [pipeline, setPipeline] = useState("");
  const server = state.data ? json(state.data.config) : "";
  const { text, dirty, conflict } = draftState(server, draft, kept);
  const parsed = parseDraft(text);
  const config = parsed.config;
  const fallback = draftPipelineNames(text);
  const name =
    pipeline in (config?.pipelines ?? {}) || fallback.includes(pipeline)
      ? pipeline
      : (config?.defaultPipeline ?? fallback[0] ?? "");
  const [validation, setValidation] = useState<{
    text: string;
    error: unknown;
    pipelines?: Record<string, unknown[]>;
  }>();
  useEffect(() => {
    if (!text) return;
    let active = true;
    const timer = setTimeout(() => {
      let body: unknown;
      try {
        body = JSON.parse(text);
      } catch {
        return;
      }
      settingsApi.validate(repo, body).then(
        (result) => {
          if (active) setValidation({ text, error: null, pipelines: result.pipelines });
        },
        (error) => {
          if (active) setValidation({ text, error });
        },
      );
    }, 300);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [repo, text]);
  const save = useMutation({
    mutationFn: (body: unknown) => api.saveConfig(repo, body),
    onSuccess: (saved) => setDraft({ text: json(saved), base: json(saved) }),
  });
  const errors = fieldErrors(save.error ?? (validation?.text === text ? validation.error : null));
  const resolved = z
    .array(stepSchema)
    .safeParse(validation?.text === text ? validation.pipelines?.[name] : undefined);
  const steps = resolved.success
    ? resolved.data
    : previewDraft(text, name, roles.data?.roles ?? []);
  useEffect(() => {
    if (draft && !dirty && draft.base === server) setDraft(undefined);
  }, [draft, dirty, server]);
  function edit(next: string) {
    save.reset();
    setDraft({ text: next, base: draft?.base ?? server });
  }
  function update(next: Config) {
    edit(json(next));
  }
  return (
    <PageShell>
      <h1 className="text-2xl font-medium">Repository settings</h1>
      <DirtyGuard dirty={dirty} />
      {state.error && <p role="alert">{state.error.message}</p>}
      {state.isPending && <p role="status">Loading settings…</p>}
      {conflict && (
        <div role="alert" className="rounded-md border border-border p-3">
          Settings changed elsewhere —{" "}
          <Button
            variant="outline"
            onClick={() => {
              setDraft(undefined);
              save.reset();
            }}
          >
            Reload
          </Button>{" "}
          <Button variant="ghost" onClick={() => setKept(server)}>
            Keep editing
          </Button>
        </div>
      )}
      {server && (
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            try {
              save.mutate(JSON.parse(text));
            } catch {
              /* The JSON error is shown above the form. */
            }
          }}
        >
          <fieldset disabled={save.isPending} className="grid gap-4">
            <label>
              Config JSON
              <textarea
                className="min-h-80 w-full rounded-md border border-border bg-input p-3 font-mono text-xs"
                spellCheck={false}
                value={text}
                onChange={(e) => edit(e.target.value)}
              />
            </label>
            {parsed.error && (
              <p role="alert" className="text-danger">
                {parsed.error}
              </p>
            )}
            {!!errors.length && (
              <ul role="alert" className="text-sm text-danger">
                {errors.map((error, i) => (
                  <li key={i}>
                    {error.path}: {error.message}
                  </li>
                ))}
              </ul>
            )}
            {config && (
              <>
                <label>
                  Default pipeline
                  <Select
                    value={config.defaultPipeline}
                    onChange={(e) => update({ ...config, defaultPipeline: e.target.value })}
                  >
                    {Object.keys(config.pipelines).map((p) => (
                      <option key={p}>{p}</option>
                    ))}
                  </Select>
                </label>
                <label>
                  Edit pipeline
                  <Select value={name} onChange={(e) => setPipeline(e.target.value)}>
                    {Object.keys(config.pipelines).map((p) => (
                      <option key={p}>{p}</option>
                    ))}
                  </Select>
                </label>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr>
                        {["Step", "Execution / Role", "Provider", "Model", "Effort"].map((h) => (
                          <th key={h} className="p-2">
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {(config.pipelines[name] ?? []).flatMap((step, si) =>
                        entries(step).map((entry, ei) => {
                          const profile = entry.profiles[entry.provider] ?? {
                            model: "",
                            effort: "",
                          };
                          function change(field: "provider" | "model" | "effort", value: string) {
                            update(editExecution(config!, name, si, ei, field, value));
                          }
                          return (
                            <tr key={`${si}/${ei}`} className="border-t border-border">
                              <td className="p-2">{step.id}</td>
                              <td className="p-2">
                                {entry.id}
                                <br />
                                <span className="text-muted-foreground">{entry.uses}</span>
                              </td>
                              <td className="p-2">
                                <Select
                                  aria-label={`${entry.id} provider`}
                                  value={entry.provider}
                                  onChange={(e) => change("provider", e.target.value)}
                                >
                                  <option>codex</option>
                                  <option>claude</option>
                                </Select>
                              </td>
                              <td className="p-2">
                                <Input
                                  aria-label={`${entry.id} model`}
                                  value={profile.model}
                                  onChange={(e) => change("model", e.target.value)}
                                />
                              </td>
                              <td className="p-2">
                                <Input
                                  aria-label={`${entry.id} effort`}
                                  value={profile.effort}
                                  onChange={(e) => change("effort", e.target.value)}
                                />
                              </td>
                            </tr>
                          );
                        }),
                      )}
                    </tbody>
                  </table>
                </div>
              </>
            )}
            <h2 className="font-medium">Pipeline preview</h2>
            <PipelineView steps={steps} invalidSteps={invalidSteps(steps, name, errors)} />
            <SettingsSaveFooter
              dirty={dirty}
              isSaving={save.isPending}
              onCancel={() => {
                setDraft(undefined);
                save.reset();
              }}
            />
          </fieldset>
        </form>
      )}
      <h2 className="font-medium">Resolved roles</h2>
      {roles.error && <p role="alert">{roles.error.message}</p>}
      <ul className="grid gap-2">
        {roles.data?.roles.map((role) => (
          <li
            key={`${role.name}/${role.provider}`}
            className="rounded-md border border-border p-3 text-sm"
          >
            <strong>{role.name}</strong> · {role.provider} · {role.type}
            <p className="break-all text-muted-foreground">{role.source}</p>
            <p>{role.status}</p>
            {role.ejectedFrom && <p className="font-mono text-xs">{role.ejectedFrom}</p>}
          </li>
        ))}
      </ul>
    </PageShell>
  );
}
