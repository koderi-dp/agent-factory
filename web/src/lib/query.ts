import { QueryClient } from "@tanstack/react-query";

// SSE owns freshness. Fetch once when a view is opened and after an explicit reset.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: Infinity,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    },
  },
});
