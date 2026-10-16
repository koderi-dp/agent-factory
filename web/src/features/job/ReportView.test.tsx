import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ReportView } from "./ReportView";
import { safeUrl } from "./report-url";
import { appendLog, renderLogLine } from "./log-lines";
describe("untrusted reports", () => {
  it("renders HTML, scripts and event handlers as inert text", () => {
    const html = renderToStaticMarkup(
      <ReportView
        text={
          '<script>alert(1)</script>\n\n<img src=x onerror="alert(2)">\n\n<div onclick="steal()">raw HTML</div>'
        }
      />,
    );
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<div onclick");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("onerror=");
  });
  it("drops dangerous URLs and all images", () => {
    const html = renderToStaticMarkup(
      <ReportView
        text={
          "[bad](javascript:alert%281%29) [data](data:text/html,evil) ![image](https://evil.test/tracking) [safe](https://example.com)"
        }
      />,
    );
    expect(html).not.toContain("javascript:");
    expect(html).not.toContain("data:text");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("tracking");
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('href="https://example.com"');
    expect(safeUrl("JaVaScRiPt:evil")).toBe("");
    expect(safeUrl("data:text/html,evil")).toBe("");
  });
  it("renders GFM and copyable path chips", () => {
    const html = renderToStaticMarkup(
      <ReportView
        text={"| file | status |\n| --- | --- |\n| src/a.ts:12 | ~~bad~~ |\n\nsrc/a.ts:12"}
      />,
    );
    expect(html).toContain("<table>");
    expect(html).toContain("<del>bad</del>");
    expect(html).toContain('title="Copy path"');
  });
});
describe("live log", () => {
  it("retains only the last 2000 lines and preserves partial records", () => {
    expect(appendLog("a", "bc\nnext")).toBe("abc\nnext");
    const result = appendLog(
      "",
      Array.from({ length: 3000 }, (_, i) => String(i)).join("\n"),
    ).split("\n");
    expect(result).toHaveLength(2000);
    expect(result[0]).toBe("1000");
  });
  it("renders messages and tool commands without injecting markup", () => {
    expect(renderLogLine('{"type":"item.completed","item":{"text":"hello"}}')).toBe("hello");
    expect(renderLogLine('{"type":"item.started","item":{"command":"git status"}}')).toContain(
      "git status",
    );
  });
});
