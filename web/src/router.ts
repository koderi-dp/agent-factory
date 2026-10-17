import {
  createRootRoute,
  createRoute,
  createRouter,
  type RouterHistory,
} from "@tanstack/react-router";
import { isSummaryKey } from "./features/overview/model";
import { Shell } from "./shell";

export function createDashboardRouter(history: RouterHistory) {
  const root = createRootRoute({ component: Shell });
  const route = (path: string) => createRoute({ getParentRoute: () => root, path });
  return createRouter({
    routeTree: root.addChildren([
      createRoute({
        getParentRoute: () => root,
        path: "/",
        validateSearch: (search: Record<string, unknown>) => ({
          ...(typeof search.state === "string" && isSummaryKey(search.state)
            ? { state: search.state }
            : {}),
        }),
      }),
      route("/repo/$repo"),
      createRoute({
        getParentRoute: () => root,
        path: "/repo/$repo/job/$job",
        validateSearch: (search: Record<string, unknown>) => ({
          ...(typeof search.node === "string" ? { node: search.node } : {}),
          ...(typeof search.file === "string" ? { file: search.file } : {}),
        }),
      }),
      route("/repo/$repo/backlog"),
      route("/repo/$repo/settings"),
      route("/settings"),
    ]),
    history,
  });
}
