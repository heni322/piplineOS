import type { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: ["/privacy/"], disallow: ["/operator", "/client", "/login", "/set-password", "/forgot-password", "/api", "/u/", "/r/"] }],
  };
}
