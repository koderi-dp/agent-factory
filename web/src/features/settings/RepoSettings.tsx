import { useQuery } from "@tanstack/react-query";
import { api, keys, settingsApi } from "@/lib/api";
import { PageShell } from "@/ui/page-shell";
import { PipelineSettingsForm } from "./PipelineSettingsForm";

export function RepoSettings({ repo }: { repo: string }) {
  const state = useQuery({ queryKey: keys.state(repo), queryFn: () => api.state(repo) });
  const roles = useQuery({
    queryKey: ["roles", repo, state.data?.config],
    queryFn: () => settingsApi.roles(repo),
  });
  return (
    <PageShell>
      <h1 className="text-2xl font-medium">Repository settings</h1>
      <PipelineSettingsForm repo={repo} showJson />
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
