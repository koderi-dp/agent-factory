import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookDown, BookUp, Plus, Trash2 } from "lucide-react";
import { libraryApi } from "@/lib/api";
import { Select } from "@/ui/primitives/select";
import { Button } from "@/ui/primitives/button";
import { Input } from "@/ui/primitives/input";
import {
  addPipeline,
  addPipelineSteps,
  canRemovePipeline,
  pipelineNameError,
  removePipeline,
  type Config,
} from "./model";

type Props = {
  config: Config;
  name: string;
  onSelect: (name: string) => void;
  onChange: (next: Config) => void;
  autoFocus?: boolean;
};

/** Create a pipeline as a copy of the selected one, or delete the selected one. */
export function PipelineManager({ config, name, onSelect, onChange, autoFocus }: Props) {
  const [newName, setNewName] = useState("");
  const error = newName ? pipelineNameError(config, newName) : undefined;
  const removable = canRemovePipeline(config, name);
  const client = useQueryClient();
  const library = useQuery({ queryKey: ["library"], queryFn: libraryApi.list });
  const [pick, setPick] = useState("");
  const picked = library.data?.pipelines.find((p) => p.name === pick);
  const pickError = picked ? pipelineNameError(config, picked.name) : undefined;
  const store = useMutation({
    mutationFn: () => libraryApi.save(name, config.pipelines[name] ?? []),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["library"] }),
  });
  const addFromLibrary = () => {
    if (!picked || pickError) return;
    onChange(addPipelineSteps(config, picked.name, picked.steps));
    onSelect(picked.name);
    setPick("");
  };
  const create = () => {
    if (pipelineNameError(config, newName)) return;
    onChange(addPipeline(config, newName, name));
    onSelect(newName);
    setNewName("");
  };
  return (
    <div className="grid gap-2 rounded-md bg-secondary/40 p-3">
      <div className="flex flex-wrap items-end gap-2">
        <label className="grid min-w-48 flex-1 gap-1 text-xs text-muted-foreground">
          New pipeline name (starts as a copy of {name})
          <Input
            aria-label="New pipeline name"
            aria-invalid={error ? true : undefined}
            value={newName}
            autoFocus={autoFocus}
            placeholder="e.g. docs-only"
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              // Enter would otherwise submit the surrounding settings form and save.
              if (e.key !== "Enter") return;
              e.preventDefault();
              create();
            }}
          />
        </label>
        <Button
          type="button"
          variant="outline"
          size="sm"
          icon={Plus}
          disabled={!newName || !!error}
          onClick={create}
        >
          Create pipeline
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          icon={Trash2}
          aria-label={`Delete pipeline ${name}`}
          title={removable ? undefined : "The default pipeline cannot be deleted"}
          disabled={!removable}
          onClick={() => onChange(removePipeline(config, name))}
        >
          Delete {name}
        </Button>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <label className="grid min-w-48 flex-1 gap-1 text-xs text-muted-foreground">
          Add a copy from the library
          <Select
            aria-label="Library pipeline"
            value={pick}
            onChange={(e) => setPick(e.target.value)}
          >
            <option value="">Select…</option>
            {library.data?.pipelines.map((p) => (
              <option key={p.name} value={p.name}>
                {p.name}
                {p.builtin ? " (built-in)" : ""}
              </option>
            ))}
          </Select>
        </label>
        <Button
          type="button"
          variant="outline"
          size="sm"
          icon={BookDown}
          disabled={!picked || !!pickError}
          onClick={addFromLibrary}
        >
          Add from library
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          icon={BookUp}
          disabled={store.isPending}
          onClick={() => store.mutate()}
        >
          Save {name} to library
        </Button>
      </div>
      {pickError && (
        <p role="alert" className="text-xs text-danger">
          {pickError} Rename it here first, or delete it.
        </p>
      )}
      {store.isSuccess && (
        <p role="status" className="text-xs text-muted-foreground">
          Saved {name} to the library.
        </p>
      )}
      {store.error && (
        <p role="alert" className="text-xs text-danger">
          Not saved to the library: {store.error.message}
        </p>
      )}
      {error && (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        Changes apply to new jobs once you save. Jobs already created keep the pipeline they started
        with.
      </p>
    </div>
  );
}
