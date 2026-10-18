import { useState } from "react";
import { Input } from "@/ui/primitives/input";
import { Select } from "@/ui/primitives/select";
import { CUSTOM_CHOICE, choiceList } from "./providers";

type Props = {
  label: string;
  value: string;
  options: readonly string[];
  onChange: (value: string) => void;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
};

/** A dropdown of known values. "Custom…" reveals a text box for anything else. */
export function ChoiceSelect({ label, value, options, onChange, ...a11y }: Props) {
  const [custom, setCustom] = useState(false);
  const list = choiceList(options, value);
  return (
    <div className="grid gap-1">
      <Select
        {...a11y}
        aria-label={label}
        value={custom ? CUSTOM_CHOICE : value}
        onChange={(e) => {
          if (e.target.value === CUSTOM_CHOICE) {
            setCustom(true);
            return;
          }
          setCustom(false);
          onChange(e.target.value);
        }}
      >
        {!value && !custom && <option value="">Select…</option>}
        {!custom &&
          list.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        {custom &&
          options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        <option value={CUSTOM_CHOICE}>Custom…</option>
      </Select>
      {custom && (
        <Input
          {...a11y}
          aria-label={`${label} custom`}
          value={value}
          autoFocus
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </div>
  );
}
