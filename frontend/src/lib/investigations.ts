import type { UIMessage } from "ai";

export type HypothesisStatus = "Leading" | "Testing" | "Dismissed";

export interface Hypothesis {
  id: string;
  title: string;
  status: HypothesisStatus;
  confidence: number;
}

export interface EvidenceItem {
  id: string;
  source: string;
  detail: string;
  value: string;
}

export interface InvestigationThread {
  id: string;
  title: string;
  summary: string;
  status: "Active" | "Review" | "Paused";
  updatedAt: string;
  hypotheses: Hypothesis[];
  evidence: EvidenceItem[];
  messages: UIMessage[];
}

export const INVESTIGATIONS_KEY = "fhi-investigation-threads-v1";

const message = (id: string, role: "user" | "assistant", text: string): UIMessage => ({
  id,
  role,
  parts: [{ type: "text", text }],
});

export const seedThreads: InvestigationThread[] = [
  {
    id: "lumber-decking-performance",
    title: "Lumber & Decking performance",
    summary: "Trace the weekly sales gap and isolate the most likely driver.",
    status: "Active",
    updatedAt: "Today, 9:42 AM",
    hypotheses: [
      {
        id: "H-01",
        title: "Demand shifted toward composite decking",
        status: "Testing",
        confidence: 58,
      },
      { id: "H-02", title: "Price elasticity on deck boards", status: "Leading", confidence: 82 },
      {
        id: "H-03",
        title: "Inventory stockout during the selected week",
        status: "Dismissed",
        confidence: 19,
      },
    ],
    evidence: [
      {
        id: "E-01",
        source: "WeeklySalesPlanActual.csv",
        detail: "Lumber & Decking · week of Aug 24",
        value: "−$4,046",
      },
      {
        id: "E-02",
        source: "InventoryHistory.csv",
        detail: "Representative decking SKUs",
        value: "14 rows",
      },
      {
        id: "E-03",
        source: "SalesOrders.csv",
        detail: "Price and volume comparison",
        value: "−6.8% vol.",
      },
    ],
    messages: [
      message(
        "seed-lumber-a",
        "assistant",
        "Lumber & Decking is **$4,046 below plan** for the selected week. The shortfall is concentrated in deck boards, while adjacent hardware remains close to target.",
      ),
      message(
        "seed-lumber-u",
        "user",
        "Compare the result with the regional benchmark and identify the leading driver.",
      ),
      message(
        "seed-lumber-b",
        "assistant",
        "The strongest current explanation is **price elasticity**. A 4.1% realized-price increase overlaps a 6.8% unit decline. I marked this as H-02, but it remains a hypothesis until the promotion and inventory records are reconciled.",
      ),
    ],
  },
  {
    id: "ukiah-building-materials",
    title: "Ukiah building materials",
    summary: "Understand margin compression without overstating causality.",
    status: "Review",
    updatedAt: "Yesterday, 4:16 PM",
    hypotheses: [
      {
        id: "H-01",
        title: "Price concessions exceeded approved bands",
        status: "Leading",
        confidence: 76,
      },
      {
        id: "H-02",
        title: "Supplier cost increase was not passed through",
        status: "Testing",
        confidence: 61,
      },
    ],
    evidence: [
      {
        id: "E-01",
        source: "WeeklySalesPlanActual.csv",
        detail: "Ukiah location variance",
        value: "−$1,805",
      },
      { id: "E-02", source: "SalesOrders.csv", detail: "Matched order lines", value: "86 rows" },
    ],
    messages: [
      message(
        "seed-ukiah-a",
        "assistant",
        "Ukiah finished **$1,805 below plan** for the selected week. Margin pressure appears concentrated in Building Materials, but the available evidence does not yet prove whether pricing or supplier cost is primary.",
      ),
    ],
  },
  {
    id: "contractor-cohort-shift",
    title: "Contractor cohort shift",
    summary: "Compare repeat contractor demand across matched weekly windows.",
    status: "Paused",
    updatedAt: "Aug 24, 1:20 PM",
    hypotheses: [
      {
        id: "H-01",
        title: "High-frequency buyers reduced project starts",
        status: "Testing",
        confidence: 44,
      },
    ],
    evidence: [
      {
        id: "E-01",
        source: "Customers.csv",
        detail: "Matched contractor cohort",
        value: "42 accounts",
      },
    ],
    messages: [
      message(
        "seed-contract-a",
        "assistant",
        "This investigation is ready for a matched-cohort comparison across the last four complete weeks.",
      ),
    ],
  },
];

export function readThreads(): InvestigationThread[] {
  if (typeof window === "undefined") return seedThreads;
  const stored = window.localStorage.getItem(INVESTIGATIONS_KEY);
  if (!stored) {
    window.localStorage.setItem(INVESTIGATIONS_KEY, JSON.stringify(seedThreads));
    return seedThreads;
  }
  try {
    const parsed = JSON.parse(stored) as InvestigationThread[];
    return parsed.length ? parsed : seedThreads;
  } catch {
    return seedThreads;
  }
}

export function writeThreads(threads: InvestigationThread[]) {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(INVESTIGATIONS_KEY, JSON.stringify(threads));
  }
}
