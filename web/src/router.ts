import {
  createRootRoute,
  createRoute,
  createRouter,
  type RouterHistory,
} from "@tanstack/react-router";
import { jobStates } from "./lib/api";
import { isSummaryKey } from "./features/overview/model";
import { Shell } from "./shell";

export function createDashboardRouter(history: RouterHistory) {
  const root = createRootRoute({ component: Shell });
  const route = (path: string) => createRoute({ getParentRoute: () => root, path });
  // Every page keeps the sidebar's repository choice in ?repo= so it survives navigation.
  const withRepo = (search: Record<string, unknown>) =>
    typeof search.repo === "string" && search.repo ? { repo: search.repo } : {};
  const scoped = (path: string) =>
    createRoute({ getParentRoute: () => root, path, validateSearch: withRepo });
  return createRouter({
    routeTree: root.addChildren([
      createRoute({
        getParentRoute: () => root,
        path: "/",
        validateSearch: (search: Record<string, unknown>) => ({
          ...(typeof search.state === "string" && isSummaryKey(search.state)
            ? { state: search.state }
            : {}),
          ...withRepo(search),
        }),
      }),
      createRoute({
        getParentRoute: () => root,
        path: "/jobs",
        validateSearch: (search: Record<string, unknown>) => ({
          ...(typeof search.state === "string" &&
          (jobStates as readonly string[]).includes(search.state)
            ? { state: search.state }
            : {}),
          ...(typeof search.repo === "string" && search.repo ? { repo: search.repo } : {}),
        }),
      }),
      createRoute({
        getParentRoute: () => root,
        path: "/activity",
        validateSearch: (search: Record<string, unknown>) => ({
          ...(typeof search.repo === "string" && search.repo ? { repo: search.repo } : {}),
          ...(typeof search.status === "string" && search.status ? { status: search.status } : {}),
        }),
      }),
      scoped("/usage"),
      scoped("/pipelines"),
      scoped("/repositories"),
      scoped("/board"),
      scoped("/backlog"),
      scoped("/repo-settings"),
      route("/repo/$repo"),
      createRoute({
        getParentRoute: () => root,
        path: "/repo/$repo/job/$job",
        validateSearch: (search: Record<string, unknown>) => ({
          ...(typeof search.node === "string" ? { node: search.node } : {}),
          ...(typeof search.file === "string" ? { file: search.file } : {}),
        }),
      }),
      createRoute({
        getParentRoute: () => root,
        path: "/repo/$repo/backlog",
        validateSearch: (search: Record<string, unknown>) => ({
          ...(search.view === "board" ? { view: "board" as const } : {}),
        }),
      }),
      route("/repo/$repo/settings"),
      scoped("/settings"),
    ]),
    history,
  });
}
