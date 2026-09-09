/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The whole app is client-side ("use client" on every page, no route handlers,
  // no middleware). Export it as a static site so it can be served straight from
  // the Vercel CDN alongside the Python API function — no Next.js server needed.
  output: "export",
  images: {
    // next/image optimization needs a server; disable it for the static export.
    unoptimized: true,
    remotePatterns: [
      { protocol: "http", hostname: "localhost" },
      { protocol: "https", hostname: "**.amazonaws.com" },
      { protocol: "https", hostname: "**.public.blob.vercel-storage.com" },
      { protocol: "https", hostname: "**.vercel.app" },
    ],
  },
};
module.exports = nextConfig;
