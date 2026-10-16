import Markdown from "react-markdown";
import { safeUrl } from "./report-url";
import remarkGfm from "remark-gfm";
import { Fragment, type ReactNode } from "react";
import { toast } from "sonner";

function chips(children: ReactNode): ReactNode {
  if (typeof children === "string")
    return children.split(/((?:[\w.-]+[\\/])+[\w.-]+:\d+)/g).map((part, i) =>
      /^(?:[\w.-]+[\\/])+[\w.-]+:\d+$/.test(part) ? (
        <button
          key={i}
          className="rounded bg-secondary px-1 font-mono text-xs"
          title="Copy path"
          onClick={() =>
            void navigator.clipboard.writeText(part).then(
              () => toast.success("Path copied"),
              () => toast.error("Could not copy path"),
            )
          }
        >
          {part}
        </button>
      ) : (
        part
      ),
    );
  if (Array.isArray(children))
    return children.map((child, i) => <Fragment key={i}>{chips(child)}</Fragment>);
  return children;
}
export function ReportView({ text }: { text: string }) {
  return (
    <div className="asset-markdown break-words">
      <Markdown
        remarkPlugins={[remarkGfm]}
        urlTransform={safeUrl}
        components={{
          a: ({ href, children }) =>
            href ? (
              <a href={href} rel="noopener noreferrer" target="_blank">
                {children}
              </a>
            ) : (
              <span>{children}</span>
            ),
          img: () => null,
          p: ({ children }) => <p>{chips(children)}</p>,
          li: ({ children }) => <li>{chips(children)}</li>,
          td: ({ children }) => <td>{chips(children)}</td>,
          code: ({ children }) => <code>{chips(children)}</code>,
        }}
      >
        {text}
      </Markdown>
    </div>
  );
}
