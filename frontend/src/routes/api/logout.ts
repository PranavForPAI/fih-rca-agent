import { createFileRoute } from "@tanstack/react-router";
import { handleLogout } from "@/lib/auth/auth.server";

export const Route = createFileRoute("/api/logout")({
  server: { handlers: { POST: () => handleLogout() } },
});
