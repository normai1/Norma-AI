export interface NavItem {
  href: string;
  label: string;
}

// Seeded with only what's actually built today; grows as later features land
// (see project-overview.md's core navigation list for the eventual target).
export const NAV_ITEMS: NavItem[] = [
  { href: "/organizations", label: "Organizations" },
  { href: "/assistants", label: "Assistants" },
  { href: "/prompt-templates", label: "Prompt Templates" },
  { href: "/settings", label: "Settings" },
];
