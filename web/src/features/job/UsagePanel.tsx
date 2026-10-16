import type { Attempt } from "@/features/board/model";
import { tokens } from "@/lib/format";
export function UsagePanel({ attempts }: { attempts: Attempt[] }) {
  return (
    <div className="overflow-auto">
      <table className="w-full text-left text-sm">
        <caption className="mb-3 text-left">{attempts.length} calls</caption>
        <thead>
          <tr>
            {["Attempt", "Input", "Cached", "Output", "Cost", "Packet bytes"].map((s) => (
              <th className="p-2" key={s}>
                {s}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {attempts.map((a) => (
            <tr key={a.id}>
              {[
                a.id,
                tokens(typeof a.usage?.input === "number" ? a.usage.input : null),
                tokens(typeof a.usage?.cached_input === "number" ? a.usage.cached_input : null),
                tokens(typeof a.usage?.output === "number" ? a.usage.output : null),
                typeof a.usage?.cost_usd === "number"
                  ? `$${a.usage.cost_usd.toFixed(4)}`
                  : "unknown",
                a.packet_bytes ?? "unknown",
              ].map((v, i) => (
                <td key={i} className="p-2 font-mono">
                  {v}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
