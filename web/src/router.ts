import {
  createRootRoute,
  createRoute,
  createRouter,
  type RouterHistory,
} from "@tanstack/react-router";
import { Shell } from "./shell";

export function createDashboardRouter(history: RouterHistory) {
  const root = createRootRoute({ component: Shell });
  const route = (path: string) => createRoute({ getParentRoute: () => root, path });
  // TODO (sessions 3.4–3.5, §13.3): replace detail/editor placeholders in Shell.
  return createRouter({
    routeTree: root.addChildren([
      route("/"),
      route("/repo/$repo"),
      route("/repo/$repo/job/$job"),
      route("/repo/$repo/backlog"),
      route("/repo/$repo/settings"),
      route("/settings"),
    ]),
    history,
  });
}
