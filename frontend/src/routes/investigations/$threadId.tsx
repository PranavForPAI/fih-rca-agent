import { createFileRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/fhi/AppShell";
import { InvestigationWorkspace } from "@/components/fhi/InvestigationWorkspace";

export const Route = createFileRoute("/investigations/$threadId")({
  head: () => ({
    meta: [
      { title: "AI Investigation | FIH agent" },
      {
        name: "description",
        content: "Topic-linked AI investigations with hypothesis tracking and an evidence ledger.",
      },
      { property: "og:title", content: "FIH agent AI Investigation" },
      {
        property: "og:description",
        content: "Evidence-aware commerce investigations organized by topic.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: InvestigationRoute,
});

function InvestigationRoute() {
  const { threadId } = Route.useParams();
  return (
    <AppShell active="investigation">
      <InvestigationWorkspace threadId={threadId} />
    </AppShell>
  );
}
