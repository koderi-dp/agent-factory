import { z } from "zod";
import { stepSchema } from "@/features/board/model";
import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, settingsApi, type PipelinePreview } from "@/lib/api";
import { Button } from "@/ui/primitives/button";
import { Select } from "@/ui/primitives/select";
import { SettingsSaveFooter } from "@/ui/settings-section";
import { PipelineView } from "@/features/job/pipeline/PipelineView";
import { DirtyGuard } from "./DirtyGuard";
import { PipelineEditor } from "./PipelineEditor";
import { PipelineManager } from "./PipelineManager";
import {
  draftPipelineNames,
  draftState,
  errorsAt,
  fieldErrors,
  invalidSteps,
  parseDraft,
  previewDraft,
  type Config,
} from "./model";

const json = (value: unknown) => JSON.stringify(value, null, 2);

type Props = {
  repo: string;
  /** Pipeline to show first; falls back to the default pipeline. */
  initialPipeline?: string;
  /** Show the raw config JSON, as repository settings does. */
  showJson?: boolean;
  /** Put the cursor in the new-pipeline name field on open. */
  focusNew?: boolean;
  /** Renders a Close button, enabled once there is nothing unsaved. */
  onClose?: () => void;
};

/** Edit, create and delete a repository's pipelines, with live validation and a preview. */
export function PipelineSettingsForm({
  repo,
  initialPipeline = "",
  showJson = false,
  focusNew = false,
  onClose,
}: Props) {
  const state = useQuery({ queryKey: keys.state(repo), queryFn: () => api.state(repo) });
  const roles = useQuery({
    queryKey: ["roles", repo, state.data?.config],
    queryFn: () => settingsApi.roles(repo),
  });
  // Asked of the installed tools, so give it time before asking again.
  const models = useQuery({
    queryKey: ["provider-models"],
    queryFn: () => settingsApi.providerModels(),
    staleTime: 10 * 60_000,
    retry: false,
  });
  const available = useQuery({
    queryKey: ["roles-available", repo, state.data?.config],
    queryFn: () => settingsApi.availableRoles(repo),
    enabled: !!state.data,
  });
  const [draft, setDraft] = useState<{ text: string; base: string }>();
  const [kept, setKept] = useState("");
  const [pipeline, setPipeline] = useState(initialPipeline);
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
    preview?: Record<string, PipelinePreview>;
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
          if (active)
            setValidation({
              text,
              error: null,
              pipelines: result.pipelines,
              preview: result.preview,
            });
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
    <>
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
            {showJson && (
              <label>
                Config JSON
                <textarea
                  className="min-h-80 w-full rounded-md border border-border bg-input p-3 font-mono text-xs"
                  spellCheck={false}
                  value={text}
                  onChange={(e) => edit(e.target.value)}
                />
              </label>
            )}
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
                  {errorsAt(errors, "defaultPipeline").map((message, i) => (
                    <span key={i} className="block text-xs text-danger">
                      {message}
                    </span>
                  ))}
                </label>
                <label>
                  Edit pipeline
                  <Select value={name} onChange={(e) => setPipeline(e.target.value)}>
                    {Object.keys(config.pipelines).map((p) => (
                      <option key={p}>{p}</option>
                    ))}
                  </Select>
                </label>
                <PipelineManager
                  config={config}
                  name={name}
                  onSelect={setPipeline}
                  onChange={update}
                  autoFocus={focusNew}
                />
                <PipelineEditor
                  config={config}
                  name={name}
                  errors={errors}
                  preview={validation?.text === text ? validation.preview?.[name] : undefined}
                  roles={roles.data?.roles ?? []}
                  catalog={models.data?.providers}
                  availableRoles={available.data?.roles}
                  onChange={update}
                />
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
            {onClose && (
              <div>
                <Button
                  type="button"
                  variant="ghost"
                  disabled={dirty}
                  title={dirty ? "Save or cancel your changes first" : undefined}
                  onClick={onClose}
                >
                  Close editor
                </Button>
              </div>
            )}
          </fieldset>
        </form>
      )}
    </>
  );
}
