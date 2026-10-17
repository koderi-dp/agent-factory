import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, keys } from "@/lib/api";
import { Button } from "@/ui/primitives/button";
import { Input } from "@/ui/primitives/input";

// Shown on an empty board: the fastest path from a fresh install to a first job.
export function StartFeature({ repo, pipeline }: { repo: string; pipeline: string }) {
  const client = useQueryClient();
  const [title, setTitle] = useState("");
  const mutation = useMutation({
    mutationFn: () => api.start(repo, { title: title.trim(), pipeline }),
    onSuccess: () => {
      setTitle("");
      void client.invalidateQueries({ queryKey: keys.state(repo) });
    },
  });
  return (
    <section
      aria-labelledby="start-feature-heading"
      className="rounded-lg bg-card p-5 shadow-[var(--shadow-border)] space-y-3"
    >
      <div className="space-y-1">
        <h2 id="start-feature-heading" className="text-base font-medium">
          Describe a feature to build
        </h2>
        <p className="text-sm text-muted-foreground">
          An agent will write a spec, build it on its own branch, review it, and wait for your
          approval before anything is merged. Pipeline: {pipeline}.
        </p>
      </div>
      <form
        className="flex flex-col gap-2 sm:flex-row"
        onSubmit={(event) => {
          event.preventDefault();
          if (title.trim()) mutation.mutate();
        }}
      >
        <label className="flex-1">
          <span className="sr-only">Feature</span>
          <Input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="e.g. Add a dark mode toggle to settings"
            disabled={mutation.isPending}
          />
        </label>
        <Button type="submit" disabled={!title.trim() || mutation.isPending}>
          {mutation.isPending ? "Starting…" : "Run"}
        </Button>
      </form>
      {mutation.error && (
        <p role="alert" className="text-sm text-danger">
          {mutation.error.message}
        </p>
      )}
    </section>
  );
}
