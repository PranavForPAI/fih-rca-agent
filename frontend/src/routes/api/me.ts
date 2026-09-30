import { createFileRoute } from "@tanstack/react-router";
import { getAuthUser } from "@/lib/auth/auth.server";

export const Route = createFileRoute("/api/me")({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const user = await getAuthUser(request);
        if (!user) return Response.json({ authenticated: false }, { status: 401 });
        return Response.json({ authenticated: true, username: user });
      },
    },
  },
});
