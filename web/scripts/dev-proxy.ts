import type { Plugin } from "vite";

/** Only the local Vite server may authenticate proxied API requests. */
export function authenticatedProxy(target: string): Plugin {
  const upstream = new URL(target);
  if (upstream.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(upstream.hostname))
    throw new Error("API_PROXY must be a local HTTP service");
  return {
    name: "factory-development-auth",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        if (!req.url?.startsWith("/api")) return next();
        const address = server.httpServer?.address();
        const port =
          typeof address === "object" && address ? address.port : server.config.server.port;
        const host = req.headers.host;
        if (
          ![`127.0.0.1:${port}`, `localhost:${port}`].includes(host ?? "") ||
          (req.headers.origin && req.headers.origin !== `http://${host}`)
        ) {
          res.statusCode = 403;
          res.end("Cross-site development API request forbidden");
          return;
        }
        if (!["GET", "HEAD", "OPTIONS"].includes(req.method ?? "GET")) {
          try {
            const response = await fetch(new URL("/", upstream), {
              signal: AbortSignal.timeout(5000),
            });
            const html = await response.text();
            const token = html.match(
              /<meta name=["']factory-token["'] content=["']([^"']+)["']/,
            )?.[1];
            if (!response.ok || !token) throw new Error("Service token unavailable");
            req.headers["x-factory-token"] = token;
            req.headers.origin = upstream.origin;
          } catch {
            res.statusCode = 502;
            res.end("Cannot authenticate to the local factory service");
            return;
          }
        }
        next();
      });
    },
  };
}
