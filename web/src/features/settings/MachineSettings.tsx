import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { settingsApi, type machineSchema } from "@/lib/api";
import type { z } from "zod";
import { PageShell } from "@/ui/page-shell";
import { Input } from "@/ui/primitives/input";
import { SettingsSaveFooter } from "@/ui/settings-section";
import { DirtyGuard } from "./DirtyGuard";
import { fieldErrors } from "./model";
type Machine = z.infer<typeof machineSchema>;
export function MachineSettings() {
  const query = useQuery({ queryKey: ["machine-settings"], queryFn: settingsApi.machine });
  const [draft, setDraft] = useState<Machine>();
  const data = draft ?? query.data;
  const dirty = !!draft && JSON.stringify(draft) !== JSON.stringify(query.data);
  const save = useMutation({
    mutationFn: settingsApi.saveMachine,
    onSuccess: (saved) => {
      setDraft(undefined);
      void query.refetch();
      return saved;
    },
  });
  function edit(next: Machine) {
    save.reset();
    setDraft(next);
  }
  return (
    <PageShell>
      <h1 className="text-2xl font-medium">Machine settings</h1>
      <DirtyGuard dirty={dirty} />
      {query.isPending && <p role="status">Loading settings…</p>}
      {query.error && <p role="alert">{query.error.message}</p>}
      {fieldErrors(save.error).map((e, i) => (
        <p key={i} role="alert" className="text-danger">
          {e.path}: {e.message}
        </p>
      ))}
      {data && (
        <form
          className="grid max-w-2xl gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate(data);
          }}
        >
          <fieldset disabled={save.isPending} className="grid gap-4">
            <label>
              Global concurrency
              <Input
                type="number"
                required
                min={1}
                max={32}
                value={data.concurrency.global}
                onChange={(e) => edit({ ...data, concurrency: { global: Number(e.target.value) } })}
              />
            </label>
            {(["codex", "claude", "opencode"] as const).map((provider) => (
              <label key={provider}>
                {provider} CLI path
                <Input
                  placeholder="Detected by factory doctor"
                  value={data.cli[provider] ?? ""}
                  onChange={(e) =>
                    edit({ ...data, cli: { ...data.cli, [provider]: e.target.value || null } })
                  }
                />
              </label>
            ))}
            <label>
              Ports range
              <Input
                required
                value={data.ports.range}
                onChange={(e) => edit({ ...data, ports: { range: e.target.value } })}
              />
            </label>
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
    </PageShell>
  );
}
