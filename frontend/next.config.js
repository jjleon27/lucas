/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Allow images served by the backend (local /files mount or S3 URL).
  images: {
    remotePatterns: [
      { protocol: "http", hostname: "localhost" },
      { protocol: "https", hostname: "**.amazonaws.com" },
      { protocol: "https", hostname: "**.railway.app" },
      { protocol: "https", hostname: "**.up.railway.app" },
    ],
  },
};
module.exports = nextConfig;
