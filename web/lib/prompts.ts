import type { Role } from "./types";

// Role-aware starters: each persona sees things it can actually do, which also demonstrates RBAC.
export const SUGGESTED: Record<Role, string[]> = {
  employee: [
    "What is the hotel cap per night in tier-1 cities?",
    "How many quotes do I need for a purchase above USD 25,000?",
    "Show my purchase requests",
    "I need to buy 2 keyboards at $45 each from vendor V-0001 for the team",
  ],
  manager: [
    "Show pending approvals",
    "Who has to approve a purchase request of USD 15,000?",
    "What is my department's remaining budget?",
    "What are the rules for single-source purchases?",
  ],
  finance: [
    "Show pending approvals",
    "What is the IT department's remaining budget?",
    "What payment terms do we target with strategic vendors?",
    "Which approvals are required above USD 100,000?",
  ],
  admin: [
    "Show pending approvals",
    "Who must approve a request above USD 250,000?",
    "What does the vendor onboarding policy require?",
    "What is the Operations budget for this fiscal year?",
  ],
};
