import { Link } from "@tanstack/react-router";
import { ArrowDownRight, ArrowRight, ArrowUpRight, Bot, CircleAlert, MapPin } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";

const metrics = [
  { label: "Sales target", value: "$139.7K", trend: "Complete week", tone: "plain" },
  { label: "Actual sales", value: "$132.7K", trend: "−5.0% vs plan", tone: "plain" },
  { label: "Exceptions", value: "7", trend: "3 critical", tone: "sun" },
  { label: "AI insights", value: "14", trend: "4 active threads", tone: "brand" },
];

const locations = [
  {
    name: "Santa Rosa",
    detail: "All categories",
    actual: "$37,727",
    variance: "−$2,660",
    width: "93%",
    color: "bg-primary",
  },
  {
    name: "Ukiah",
    detail: "Building Materials",
    actual: "$27,658",
    variance: "−$1,805",
    width: "94%",
    color: "bg-azure",
  },
  {
    name: "Petaluma",
    detail: "All categories",
    actual: "$35,033",
    variance: "−$1,713",
    width: "95%",
    color: "bg-accent",
  },
  {
    name: "Sonoma",
    detail: "All categories",
    actual: "$32,239",
    variance: "−$866",
    width: "97%",
    color: "bg-mint",
  },
];

const categories = [
  {
    name: "Lumber & Decking",
    state: "Watch",
    note: "−$4,046 vs plan",
    className: "bg-accent/10 text-accent",
  },
  {
    name: "Building Materials",
    state: "Watch",
    note: "−$1,906 vs plan",
    className: "bg-sun/20 text-foreground",
  },
  {
    name: "Hardware & Tools",
    state: "Stable",
    note: "−$368 vs plan",
    className: "bg-mint/10 text-mint",
  },
];

export function Dashboard() {
  const [period, setPeriod] = useState("Week");
  const [scope, setScope] = useState("All stores");
  return (
    <div className="min-h-screen overflow-hidden">
      <header className="flex flex-wrap items-end justify-between gap-5 px-5 pb-5 pt-7 md:px-8">
        <div>
          <p className="text-[11px] font-bold uppercase text-primary">
            Commerce operations · Week of Aug 24, 2026
          </p>
          <h1 className="mt-2 font-display text-4xl font-black md:text-[44px]">
            Executive overview
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="glass flex rounded-[8px] p-1">
            {["Week", "Month", "Quarter"].map((item) => (
              <Button
                key={item}
                size="sm"
                variant={period === item ? "default" : "ghost"}
                onClick={() => setPeriod(item)}
                className="rounded-[6px]"
              >
                {item}
              </Button>
            ))}
          </div>
          <label className="glass flex h-10 items-center gap-2 rounded-[8px] px-3 text-xs font-semibold">
            <MapPin className="size-3.5 text-mint" />
            <span className="sr-only">Location</span>
            <select
              value={scope}
              onChange={(event) => setScope(event.target.value)}
              className="bg-transparent outline-none"
            >
              <option>All stores</option>
              <option>Ukiah</option>
              <option>Santa Rosa</option>
              <option>Petaluma</option>
              <option>Sonoma</option>
            </select>
          </label>
        </div>
      </header>

      <section
        className="grid grid-cols-1 gap-4 px-5 sm:grid-cols-2 xl:grid-cols-4 md:px-8"
        aria-label="Headline metrics"
      >
        {metrics.map((metric) => (
          <article
            key={metric.label}
            className={`metric-panel p-5 ${metric.tone === "sun" ? "bg-sun text-sun-foreground" : metric.tone === "brand" ? "bg-primary text-primary-foreground" : "glass"}`}
          >
            <p className="text-[11px] font-bold uppercase opacity-55">{metric.label}</p>
            <p className="mt-2 font-display text-4xl font-black tabular-nums">{metric.value}</p>
            <div className="mt-3 flex items-center gap-1 text-xs font-bold opacity-80">
              {metric.trend.startsWith("−") ? (
                <ArrowDownRight className="size-3.5" />
              ) : metric.trend.includes("active") ? (
                <Bot className="size-3.5" />
              ) : (
                <ArrowUpRight className="size-3.5" />
              )}
              {metric.trend}
            </div>
          </article>
        ))}
      </section>

      <section className="grid grid-cols-1 gap-5 px-5 py-6 xl:grid-cols-12 md:px-8">
        <div className="space-y-5 xl:col-span-7">
          <article id="locations" className="glass p-5">
            <div className="mb-5 flex items-center justify-between">
              <div>
                <h2 className="font-display text-lg font-extrabold">Location performance</h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  Actual sales against weekly plan
                </p>
              </div>
              <span className="text-[11px] font-semibold text-muted-foreground">
                {scope} · {period}
              </span>
            </div>
            <div className="space-y-4">
              {locations.map((location, index) => (
                <div
                  key={location.name}
                  className="grid grid-cols-[24px_1fr_auto] items-center gap-3"
                >
                  <span className="font-display text-lg font-black text-muted-foreground">
                    {index + 1}
                  </span>
                  <div>
                    <div className="flex flex-wrap justify-between gap-2 text-sm font-bold">
                      <span>
                        {location.name}{" "}
                        <span className="font-normal text-muted-foreground">
                          · {location.detail}
                        </span>
                      </span>
                      <span>{location.actual}</span>
                    </div>
                    <div className="mt-2 h-2 overflow-hidden rounded-full bg-secondary">
                      <div
                        className={`h-full rounded-full ${location.color}`}
                        style={{ width: location.width }}
                      />
                    </div>
                  </div>
                  <span className="w-16 text-right text-xs font-bold text-accent">
                    {location.variance}
                  </span>
                </div>
              ))}
            </div>
          </article>
          <article id="categories" className="glass p-5">
            <h2 className="font-display text-lg font-extrabold">Category health</h2>
            <div className="mt-4 grid gap-3 md:grid-cols-3">
              {categories.map((category) => (
                <div key={category.name} className={`rounded-[8px] p-4 ${category.className}`}>
                  <p className="text-xs font-bold opacity-70">{category.name}</p>
                  <p className="mt-2 font-display text-2xl font-black">{category.state}</p>
                  <p className="mt-1 text-[11px] font-semibold opacity-65">{category.note}</p>
                </div>
              ))}
            </div>
          </article>
        </div>
        <div className="space-y-5 xl:col-span-5">
          <article
            id="exceptions"
            className="rounded-[8px] bg-sidebar p-5 text-sidebar-foreground shadow-xl"
          >
            <div className="flex items-center justify-between">
              <h2 className="font-display text-lg font-extrabold">Exception monitoring</h2>
              <span className="rounded-[6px] bg-accent px-2 py-1 text-[11px] font-bold text-accent-foreground">
                3 critical
              </span>
            </div>
            <div className="mt-4 space-y-2.5">
              {[
                [
                  "Ukiah margin below threshold",
                  "Building Materials · 2.3 pts under target",
                  "bg-accent",
                ],
                ["Lumber stockout risk", "Healdsburg · 4 SKUs under 3-day cover", "bg-sun"],
                ["Price mismatch flagged", "Anderson · 2 items vs list", "bg-azure"],
              ].map(([title, note, tone]) => (
                <div
                  key={title}
                  className="flex items-start gap-3 rounded-[8px] bg-sidebar-accent p-3"
                >
                  <span className={`mt-1 size-2.5 shrink-0 rounded-full ${tone}`} />
                  <div>
                    <p className="text-sm font-bold">{title}</p>
                    <p className="mt-1 text-[11px] text-sidebar-muted">{note}</p>
                  </div>
                </div>
              ))}
            </div>
          </article>
          <article className="glass border-primary/20 p-5">
            <div className="flex items-center gap-3">
              <div className="grid size-8 place-items-center rounded-[8px] bg-primary font-display text-xs font-black text-primary-foreground">
                AI
              </div>
              <div>
                <h2 className="font-display text-lg font-extrabold">Investigation brief</h2>
                <p className="text-[11px] font-semibold text-primary">Evidence-aware analysis</p>
              </div>
            </div>
            <p className="mt-4 text-sm leading-6 text-muted-foreground">
              Lumber & Decking accounts for 57% of the weekly category gap. Ukiah’s store-level miss
              is meaningful, but the current evidence does not establish a single cause.
            </p>
            <div className="mt-4 rounded-[8px] bg-secondary/70 p-3 text-xs font-semibold">
              <CircleAlert className="mr-2 inline size-4 text-sun" />
              Separate observed variance from causal hypotheses.
            </div>
            <Button asChild className="mt-4 w-full justify-between">
              <Link
                to="/investigations/$threadId"
                params={{ threadId: "lumber-decking-performance" }}
              >
                Open AI investigation <ArrowRight />
              </Link>
            </Button>
          </article>
        </div>
      </section>
    </div>
  );
}
