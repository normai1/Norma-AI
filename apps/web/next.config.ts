import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /**
   * Development only: never let a browser reuse a cached copy of a page.
   *
   * A stale bundle is indistinguishable, from the outside, from a fix that
   * did not work - and several rounds of test-call debugging were spent on
   * exactly that confusion, with the voice worker recording a client build
   * two releases behind while fixes were being tested against it. Asking
   * for a hard refresh proved unreliable; this removes the failure mode.
   *
   * Production is untouched: it has content-hashed assets and wants its
   * caching.
   */
  async headers() {
    if (process.env.NODE_ENV !== "development") {
      return [];
    }

    return [
      {
        source: "/:path*",
        headers: [{ key: "Cache-Control", value: "no-store, must-revalidate" }],
      },
    ];
  },
};

export default nextConfig;
