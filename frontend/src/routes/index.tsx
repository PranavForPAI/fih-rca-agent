import { createFileRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/fhi/AppShell";
import { Dashboard } from "@/components/fhi/Dashboard";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Executive Overview | FIH agent" },
      {
        name: "description",
        content:
          "Commerce operations performance, category health, exceptions, and evidence-aware AI investigations.",
      },
      { property: "og:title", content: "FIH agent Commerce Intelligence" },
      {
        property: "og:description",
        content: "A focused workspace for commerce operations and evidence-aware investigations.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Index,
});

function Index() {
  return (
    <AppShell active="overview">
      <Dashboard />
    </AppShell>
  );
}
