import { Link } from "@tanstack/react-router";
import {
  AlertTriangle,
  BarChart3,
  Bot,
  Boxes,
  LayoutDashboard,
  MapPin,
  Menu,
  X,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const navItems = [
  { label: "Executive overview", icon: LayoutDashboard, to: "/", hash: undefined },
  { label: "Location performance", icon: MapPin, to: "/", hash: "locations" },
  { label: "Category health", icon: Boxes, to: "/", hash: "categories" },
  { label: "Exceptions", icon: AlertTriangle, to: "/", hash: "exceptions" },
  {
    label: "AI investigation",
    icon: Bot,
    to: "/investigations/lumber-decking-performance",
    hash: undefined,
  },
];

export function BrandMark({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <div className="grid size-10 place-items-center rounded-[8px] bg-accent font-display text-xs font-black text-accent-foreground shadow-[0_0_26px_var(--accent-glow)]">
        FIH
      </div>
      {!compact && (
        <div>
          <p className="font-display text-base font-black leading-none text-sidebar-foreground">
            FIH agent
          </p>
          <p className="mt-1 text-[10px] font-semibold uppercase text-sidebar-muted">
            Commerce intelligence
          </p>
        </div>
      )}
    </div>
  );
}

export function AppShell({
  children,
  active,
}: {
  children: ReactNode;
  active: "overview" | "investigation";
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex h-dvh min-h-0 flex-col overflow-hidden bg-background text-foreground lg:flex-row">
      <header className="z-40 flex h-16 shrink-0 items-center justify-between border-b border-border bg-background/85 px-4 backdrop-blur-xl lg:hidden">
        <BrandMark />
        <Button
          size="icon"
          variant="ghost"
          onClick={() => setOpen((value) => !value)}
          aria-label="Toggle navigation"
        >
          {open ? <X /> : <Menu />}
        </Button>
      </header>
      <aside
        className={cn(
          "fixed bottom-0 left-0 top-16 z-30 flex w-[238px] -translate-x-full flex-col overflow-y-auto bg-sidebar p-4 text-sidebar-foreground transition-transform lg:static lg:h-dvh lg:shrink-0 lg:translate-x-0",
          open && "translate-x-0",
        )}
      >
        <div className="px-2 pt-1">
          <BrandMark />
        </div>
        <nav className="mt-8 flex flex-col gap-1.5" aria-label="Primary navigation">
          {navItems.map((item) => {
            const isActive = item.label.includes("AI")
              ? active === "investigation"
              : item.label.includes("Executive") && active === "overview";
            const Icon = item.icon;
            return (
              <Link
                key={item.label}
                to={item.to}
                {...(item.hash ? { hash: item.hash } : {})}
                onClick={() => setOpen(false)}
                className={cn(
                  "flex items-center gap-3 rounded-[8px] px-3 py-2.5 text-sm font-semibold text-sidebar-muted transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground",
                  isActive &&
                    "bg-sidebar-primary text-sidebar-primary-foreground shadow-[0_8px_24px_var(--brand-glow)]",
                )}
              >
                <Icon className="size-4" />
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto border-t border-sidebar-border pt-4">
          <div className="flex items-center gap-3 rounded-[8px] bg-sidebar-accent p-3">
            <div className="grid size-8 place-items-center rounded-full bg-sun font-display text-xs font-black text-sun-foreground">
              DO
            </div>
            <div>
              <p className="text-sm font-bold text-sidebar-foreground">Dana Okafor</p>
              <p className="text-[11px] text-sidebar-muted">Regional Ops Lead</p>
            </div>
          </div>
          <div className="mt-3 flex items-center gap-2 px-1 text-[10px] font-semibold uppercase text-sidebar-muted">
            <BarChart3 className="size-3" /> Synthetic demonstration
          </div>
        </div>
      </aside>
      <main className="min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-contain">{children}</main>
    </div>
  );
}
