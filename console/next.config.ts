import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static export. Every page in app/ is a client component talking to the
  // FastAPI service over fetch -- there are no server components, server
  // actions, or route handlers -- so there is nothing that needs a Node
  // runtime at request time. Exporting to plain HTML/CSS/JS lets the same
  // Lambda that serves the API also serve the console, so the deployed
  // demo is ONE url instead of a JSON API on one host and a UI on another.
  output: "export",

  // Static export has no image optimization server.
  images: { unoptimized: true },

  // Emit `/timeline/index.html` rather than `/timeline.html`, so a request
  // for `/timeline` resolves without any server-side rewrite rule --
  // Starlette's StaticFiles(html=True) serves the directory's index.html.
  trailingSlash: true,
};

export default nextConfig;
