/**
 * The Knowledge tab shows the assistant's FAQs themselves, not the
 * containers they are filed under.
 *
 * Written after shipping this screen unverified and being told "no faqs
 * created" while the database held them. The page is behind a login, so a
 * browser cannot reach it here - but the component can, and that is where
 * this should have been checked in the first place.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listFaqEntries = vi.fn();
const listKnowledgeSources = vi.fn();
const createFaqEntry = vi.fn();
const createManualFaqKnowledgeSource = vi.fn();

vi.mock("next/navigation", () => ({
  useParams: () => ({ assistantId: "assistant-1" }),
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/components/app/tenant-provider", () => ({
  useTenant: () => ({
    activeWorkspace: {
      id: "ws-1",
      organization_id: "org-1",
      name: "W",
      settings: { locale: "en-US", timezone: "UTC", business_hours: null },
    },
    organizations: [],
    loading: false,
  }),
}));

vi.mock("@/lib/assistants", () => ({
  getAssistant: vi.fn(async () => ({
    id: "assistant-1",
    name: "AIRMA",
    status: "draft",
    voice_id: null,
    language: "en",
    greeting: "",
    persona: "",
    custom_prompt: "",
    speech_rate: 1,
    turn_sensitivity: 0.5,
    creativity: 0.3,
    ambient_sound: null,
    ambient_sound_volume: 0,
    blocked_topics: [],
  })),
  archiveAssistant: vi.fn(),
  deleteAssistant: vi.fn(),
  publishAssistant: vi.fn(),
  renameAssistant: vi.fn(),
  updateAssistant: vi.fn(),
}));

vi.mock("@/lib/glossary", () => ({
  createGlossaryEntry: vi.fn(),
  deleteGlossaryEntry: vi.fn(),
  listGlossaryEntries: vi.fn(async () => []),
  updateGlossaryEntry: vi.fn(),
}));

vi.mock("@/lib/voices", () => ({ listVoices: vi.fn(async () => []) }));

vi.mock("@/lib/knowledge-sources", async (importOriginal) => {
  // The pure helpers are the thing under test; only the network is faked.
  const actual =
    await importOriginal<typeof import("@/lib/knowledge-sources")>();

  return {
    ...actual,
    listKnowledgeSources,
    listFaqEntries,
    createFaqEntry,
    createManualFaqKnowledgeSource,
    deleteKnowledgeSource: vi.fn(),
    processKnowledgeSource: vi.fn(),
    recrawlKnowledgeSource: vi.fn(),
    uploadKnowledgeSourceFile: vi.fn(),
    createWebsiteKnowledgeSource: vi.fn(),
    updateFaqEntry: vi.fn(),
    deleteFaqEntry: vi.fn(),
  };
});

const { default: AssistantDetailPage } = await import("./page");
const { FAQ_POLL_INTERVAL_MS } = await import("./page");

function source(overrides: Record<string, unknown> = {}) {
  return {
    id: "src-1",
    organization_id: "org-1",
    workspace_id: "ws-1",
    assistant_id: "assistant-1",
    type: "file",
    status: "completed",
    name: null,
    url: null,
    error_message: null,
    created_at: "2026-09-11T00:00:00Z",
    updated_at: "2026-09-11T00:00:00Z",
    ...overrides,
  };
}

function entry(overrides: Record<string, unknown> = {}) {
  return {
    id: "faq-1",
    knowledge_source_id: "container-1",
    question: "What are your opening hours?",
    answer: "Nine to five.",
    created_at: "2026-09-11T00:00:00Z",
    generated_from_knowledge_source_id: null,
    ...overrides,
  };
}

async function openKnowledgeTab() {
  render(<AssistantDetailPage />);

  // Two tabs are called Knowledge - the assistant's top-level one and the
  // sub-tab inside it - so neither can be found by name alone. The
  // top-level is first in the document, the sub-tab last.
  await waitFor(() =>
    expect(screen.getAllByRole("tab", { name: /^Knowledge/ }).length).toBe(1),
  );
  await userEvent.click(screen.getAllByRole("tab", { name: /^Knowledge/ })[0]);

  const withSubTab = screen.getAllByRole("tab", { name: /^Knowledge/ });

  await userEvent.click(withSubTab[withSubTab.length - 1]);
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  // Not automatic here: without it each test renders another copy of the
  // page into the same document and every query finds them all.
  cleanup();
});

describe("the Knowledge tab", () => {
  it("shows the generated FAQs themselves, not the container they live in", async () => {
    listKnowledgeSources.mockResolvedValue([
      source({ id: "pdf-1", type: "file" }),
      source({ id: "container-1", type: "manual_faq", name: "Generated FAQs" }),
    ]);
    listFaqEntries.mockResolvedValue([
      entry({ id: "faq-1", question: "What are your opening hours?" }),
      entry({ id: "faq-2", question: "How much does the plan cost?" }),
    ]);

    await openKnowledgeTab();

    expect(
      await screen.findByText("What are your opening hours?"),
    ).toBeDefined();
    expect(screen.getByText("How much does the plan cost?")).toBeDefined();
    // The container's own name is an implementation detail.
    expect(screen.queryByText("Generated FAQs")).toBeNull();
  });

  it("merges entries from every container into one list", async () => {
    listKnowledgeSources.mockResolvedValue([
      source({ id: "container-1", type: "manual_faq", name: "Generated FAQs" }),
      source({ id: "container-2", type: "manual_faq", name: "General FAQ" }),
    ]);
    listFaqEntries.mockImplementation(async (_o, _w, id: string) =>
      id === "container-1"
        ? [entry({ id: "a", question: "From the document?" })]
        : [entry({ id: "b", question: "Written by hand?" })],
    );

    await openKnowledgeTab();

    expect(await screen.findByText("From the document?")).toBeDefined();
    expect(screen.getByText("Written by hand?")).toBeDefined();
  });

  it("counts the FAQs on the tab itself", async () => {
    listKnowledgeSources.mockResolvedValue([
      source({ id: "container-1", type: "manual_faq" }),
    ]);
    listFaqEntries.mockResolvedValue([
      entry({ id: "a" }),
      entry({ id: "b" }),
      entry({ id: "c" }),
    ]);

    render(<AssistantDetailPage />);

    await waitFor(() =>
      expect(screen.getAllByRole("tab", { name: /^Knowledge/ }).length).toBe(1),
    );
    await userEvent.click(screen.getAllByRole("tab", { name: /^Knowledge/ })[0]);

    // The count is what someone opens this screen to check after an upload,
    // and it is on the tab so it is readable without opening the tab at all.
    expect(
      await screen.findByRole("tab", { name: "Knowledge (3)" }),
    ).toBeDefined();
  });

  it("says so plainly when an assistant really has none", async () => {
    listKnowledgeSources.mockResolvedValue([source({ type: "file" })]);

    await openKnowledgeTab();

    expect(await screen.findByText("No FAQs yet.")).toBeDefined();
    expect(listFaqEntries).not.toHaveBeenCalled();
  });

  it("picks up FAQs written after the page was already open", async () => {
    // The reason this whole file exists. Parsing and embedding finish inside
    // the upload; the questions are written by a background job minutes
    // later, and the source-status poll has long since stopped by then -
    // because the source itself completed before generation even started.
    listKnowledgeSources.mockResolvedValue([
      source({ id: "pdf-1", type: "file" }),
      source({ id: "container-1", type: "manual_faq" }),
    ]);
    listFaqEntries.mockResolvedValue([]);

    await openKnowledgeTab();
    await screen.findByText("No FAQs yet.");

    const callsBefore = listFaqEntries.mock.calls.length;

    listFaqEntries.mockResolvedValue([
      entry({ id: "a", question: "Arrived a few minutes later?" }),
    ]);

    await waitFor(
      () => {
        expect(listFaqEntries.mock.calls.length).toBeGreaterThan(callsBefore);
      },
      { timeout: FAQ_POLL_INTERVAL_MS * 2 },
    );

    expect(
      await screen.findByText("Arrived a few minutes later?"),
    ).toBeDefined();
  }, 30_000);

  it("keeps the badge naming the document a question came from", async () => {
    listKnowledgeSources.mockResolvedValue([
      source({ id: "pdf-1", type: "file", name: "handbook.pdf" }),
      source({ id: "container-1", type: "manual_faq" }),
    ]);
    listFaqEntries.mockResolvedValue([
      entry({ generated_from_knowledge_source_id: "pdf-1" }),
    ]);

    await openKnowledgeTab();

    expect(await screen.findByText(/From/)).toBeDefined();
  });
});
