import { createFileRoute } from "@tanstack/react-router";
import { handleLogin } from "@/lib/auth/auth.server";

export const Route = createFileRoute("/api/login")({
  server: { handlers: { POST: ({ request }) => handleLogin(request) } },
});
