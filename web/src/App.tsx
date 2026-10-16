import { createHashHistory, RouterProvider } from "@tanstack/react-router";
import { createDashboardRouter } from "./router";

const router = createDashboardRouter(createHashHistory());

export function App() {
  return <RouterProvider router={router} />;
}
