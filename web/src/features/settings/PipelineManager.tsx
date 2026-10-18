import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/ui/primitives/button";
import { Input } from "@/ui/primitives/input";
import {
  addPipeline,
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
