import { test as base, expect } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createServer } from "node:net";

export const test = base.extend<{
  engine: { url: string; repos: string[]; restart: () => Promise<void>; stop: () => Promise<void> };
}>({
  engine: async ({ playwright: _playwright }, provide) => {
    const root = await mkdtemp(join(tmpdir(), "factory-e2e-"));
    const socket = createServer();
    await new Promise<void>((done) => socket.listen(0, "127.0.0.1", done));
    const port = (socket.address() as { port: number }).port;
    await new Promise<void>((done) => socket.close(() => done()));
    const url = `http://127.0.0.1:${port}`;
    const python = resolve(
      "../.venv",
      process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
    );
    let child: ChildProcess;
    let logs = "";
    async function start() {
      child = spawn(python, ["e2e_server.py", "--root", root, "--port", String(port)], {
        stdio: ["ignore", "pipe", "pipe"],
      });
      child.stdout?.on("data", (data) => (logs += data));
      child.stderr?.on("data", (data) => (logs += data));
      await expect
        .poll(
          async () => {
            if (child.exitCode !== null) throw new Error(logs);
            return fetch(`${url}/api/health`)
              .then((r) => r.ok)
              .catch(() => false);
          },
          { timeout: 30_000, message: "Engine startup" },
        )
        .toBe(true);
    }
    async function stop() {
      if (child.exitCode !== null) return;
      const exited = new Promise<void>((done) => child.once("exit", () => done()));
      child.kill("SIGKILL");
      await exited;
    }
    try {
      await start();
      const repos = JSON.parse(await readFile(join(root, "ready.json"), "utf8")) as string[];
      await provide({ url, repos, stop, restart: start });
    } finally {
      await stop();
      // Stop uses the real engine ownership protocol before removing its worktrees.
      await start();
      const overview = await fetch(`${url}/api/overview`).then((r) => r.json());
      for (const repo of overview.repos) {
        const state = await fetch(`${url}/api/repos/${repo.id}/state`).then((r) => r.json());
        for (const job of state.jobs) {
          if (["running", "queued"].includes(job.state))
            await fetch(`${url}/api/repos/${repo.id}/jobs/${job.id}/stop`, {
              method: "POST",
              headers: { "X-Factory-Token": "e2e-token", "Content-Type": "application/json" },
              body: "{}",
            });
        }
      }
      await expect
        .poll(async () => {
          for (const repo of overview.repos) {
            const state = await fetch(`${url}/api/repos/${repo.id}/state`).then((r) => r.json());
            if (
              state.executions.some((e: { state: string }) =>
                ["claimed", "running"].includes(e.state),
              )
            )
              return false;
          }
          return true;
        })
        .toBe(true);
      await stop();
      await rm(root, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
    }
  },
});
export { expect };
