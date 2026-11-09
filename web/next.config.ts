import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // The app talks to the gateway from the browser with a bearer token; no Next server routes.
  poweredByHeader: false,
  // Dev only: without this, opening the app at 127.0.0.1 (not localhost) blocks hydration and the UI stays inert.
  allowedDevOrigins: ["127.0.0.1"],
};

export default config;
