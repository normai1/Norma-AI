import { describe, expect, it } from "vitest";

import {
  canRecrawlKnowledgeSource,
  canRetryKnowledgeSource,
  faqEntryOriginLabel,
  isKnowledgeSourceProcessing,
  knowledgeSourceStatusLabel,
  knowledgeSourceDisplayName,
  knowledgeSourceTypeLabel,
  type FaqEntry,
  type KnowledgeSource,
  faqCountLabel,
  manualFaqSources,
} from "./knowledge-sources";

function makeSource(overrides: Partial<KnowledgeSource> = {}): KnowledgeSource {
  return {
    id: "source-1",
    organization_id: "org-1",
    workspace_id: "ws-1",
    assistant_id: "assistant-1",
    type: "file",
    status: "completed",
    error_message: null,
    owner_user_id: "user-1",
    source_url: null,
    name: null,
    created_at: "2026-01-01T00:00:00Z",
    document: null,
    crawled_pages: null,
    ...overrides,
  };
}

describe("canRetryKnowledgeSource", () => {
  it("allows retry for a failed file source", () => {
    expect(
      canRetryKnowledgeSource(makeSource({ type: "file", status: "failed" })),
    ).toBe(true);
  });

  it("disallows retry for a completed file source", () => {
    expect(
      canRetryKnowledgeSource(makeSource({ type: "file", status: "completed" })),
    ).toBe(false);
  });

  it("disallows retry for a failed website source", () => {
    expect(
      canRetryKnowledgeSource(makeSource({ type: "website", status: "failed" })),
    ).toBe(false);
  });

  it("disallows retry for a failed manual-FAQ source", () => {
    expect(
      canRetryKnowledgeSource(makeSource({ type: "manual_faq", status: "failed" })),
    ).toBe(false);
  });
});

describe("canRecrawlKnowledgeSource", () => {
  it("allows recrawl for a website source regardless of status", () => {
    expect(
      canRecrawlKnowledgeSource(makeSource({ type: "website", status: "completed" })),
    ).toBe(true);
    expect(
      canRecrawlKnowledgeSource(makeSource({ type: "website", status: "failed" })),
    ).toBe(true);
  });

  it("disallows recrawl for a file source", () => {
    expect(canRecrawlKnowledgeSource(makeSource({ type: "file" }))).toBe(false);
  });

  it("disallows recrawl for a manual-FAQ source", () => {
    expect(canRecrawlKnowledgeSource(makeSource({ type: "manual_faq" }))).toBe(false);
  });
});

describe("knowledgeSourceTypeLabel", () => {
  it("labels every known type", () => {
    expect(knowledgeSourceTypeLabel("file")).toBe("File");
    expect(knowledgeSourceTypeLabel("website")).toBe("Website");
    expect(knowledgeSourceTypeLabel("manual_faq")).toBe("Manual FAQ");
  });
});

describe("knowledgeSourceDisplayName", () => {
  it("prefers name when present", () => {
    expect(
      knowledgeSourceDisplayName(
        makeSource({ name: "General FAQ", document: null, source_url: null }),
      ),
    ).toBe("General FAQ");
  });

  it("falls back to the document filename when name is absent", () => {
    expect(
      knowledgeSourceDisplayName(
        makeSource({
          name: null,
          document: {
            id: "doc-1",
            filename: "policy.txt",
            content_type: "text/plain",
            processing_status: "completed",
            processing_error: null,
            created_at: "2026-01-01T00:00:00Z",
          },
        }),
      ),
    ).toBe("policy.txt");
  });

  it("falls back to the source URL when name and document are absent", () => {
    expect(
      knowledgeSourceDisplayName(
        makeSource({ name: null, document: null, source_url: "http://example.com/" }),
      ),
    ).toBe("http://example.com/");
  });

  it("falls back to a generic label when nothing identifying is present", () => {
    expect(
      knowledgeSourceDisplayName(
        makeSource({ name: null, document: null, source_url: null }),
      ),
    ).toBe("Untitled source");
  });
});

function makeFaqEntry(overrides: Partial<FaqEntry> = {}): FaqEntry {
  return {
    id: "faq-1",
    knowledge_source_id: "generated-container",
    question: "What are your hours?",
    answer: "9am to 5pm.",
    created_at: "2026-01-01T00:00:00Z",
    generated_from_knowledge_source_id: null,
    ...overrides,
  };
}

describe("faqEntryOriginLabel", () => {
  it("returns null for an operator-written entry", () => {
    expect(faqEntryOriginLabel(makeFaqEntry(), [])).toBeNull();
  });

  it("names the file a generated entry came from", () => {
    const source = makeSource({
      id: "source-9",
      document: {
        id: "doc-1",
        filename: "handbook.pdf",
        content_type: "application/pdf",
        processing_status: "completed",
        processing_error: null,
        created_at: "2026-01-01T00:00:00Z",
      },
    });

    const entry = makeFaqEntry({ generated_from_knowledge_source_id: "source-9" });

    expect(faqEntryOriginLabel(entry, [source])).toBe("handbook.pdf");
  });

  it("names the site a generated entry came from", () => {
    const source = makeSource({
      id: "source-9",
      type: "website",
      source_url: "https://example.com",
    });

    const entry = makeFaqEntry({ generated_from_knowledge_source_id: "source-9" });

    expect(faqEntryOriginLabel(entry, [source])).toBe("https://example.com");
  });

  it("returns null when the origin source is not among those loaded", () => {
    const entry = makeFaqEntry({ generated_from_knowledge_source_id: "missing" });

    expect(faqEntryOriginLabel(entry, [makeSource()])).toBeNull();
  });
});

describe("isKnowledgeSourceProcessing", () => {
  it("is true for a website that is still being crawled", () => {
    expect(
      isKnowledgeSourceProcessing(
        makeSource({ type: "website", status: "pending", error_message: null }),
      ),
    ).toBe(true);
  });

  it("is true while a source is processing", () => {
    expect(
      isKnowledgeSourceProcessing(makeSource({ status: "processing" })),
    ).toBe(true);
  });

  it("is false once a source completes", () => {
    expect(isKnowledgeSourceProcessing(makeSource({ status: "completed" }))).toBe(
      false,
    );
  });

  it("is false for a failed source", () => {
    expect(isKnowledgeSourceProcessing(makeSource({ status: "failed" }))).toBe(
      false,
    );
  });

  it("is false for a pending source parked on an error", () => {
    // These wait for the operator, not for a job - polling them never ends.
    expect(
      isKnowledgeSourceProcessing(
        makeSource({
          status: "pending",
          error_message: "Embedding provider changed; this source needs reprocessing.",
        }),
      ),
    ).toBe(false);
  });
});

describe("knowledgeSourceStatusLabel", () => {
  it("says the knowledge base is being built while a crawl runs", () => {
    // "pending" is a database word; the operator just pasted a URL and needs
    // to know something is happening.
    expect(
      knowledgeSourceStatusLabel(
        makeSource({ type: "website", status: "pending", error_message: null }),
      ),
    ).toBe("Creating knowledge base...");
  });

  it("reports completed once the questions and answers exist", () => {
    // FAQ generation finishes before the status flips, so this is honest.
    expect(knowledgeSourceStatusLabel(makeSource({ status: "completed" }))).toBe(
      "completed",
    );
  });

  it("distinguishes a source parked on an error from one in flight", () => {
    expect(
      knowledgeSourceStatusLabel(
        makeSource({
          status: "pending",
          error_message: "Embedding provider changed; this source needs reprocessing.",
        }),
      ),
    ).toBe("needs reprocessing");
  });

  it("passes a failure through", () => {
    expect(knowledgeSourceStatusLabel(makeSource({ status: "failed" }))).toBe(
      "failed",
    );
  });
});

describe("manualFaqSources", () => {
  it("returns only the containers FAQ entries live in", () => {
    const sources = [
      makeSource({ id: "file-1", type: "file" }),
      makeSource({ id: "faq-1", type: "manual_faq" }),
      makeSource({ id: "site-1", type: "website" }),
      makeSource({ id: "faq-2", type: "manual_faq" }),
    ];

    expect(manualFaqSources(sources).map((source) => source.id)).toEqual([
      "faq-1",
      "faq-2",
    ]);
  });

  it("returns nothing when an assistant has no FAQs at all", () => {
    expect(manualFaqSources([makeSource({ type: "file" })])).toEqual([]);
  });
});

describe("faqCountLabel", () => {
  it("shows the total alongside the label", () => {
    expect(faqCountLabel("Knowledge", [makeFaqEntry(), makeFaqEntry()])).toBe(
      "Knowledge (2)",
    );
  });

  it("shows zero once the list is known to be empty", () => {
    expect(faqCountLabel("Knowledge", [])).toBe("Knowledge (0)");
  });

  it("shows no count while the list is still loading", () => {
    // "(0)" here would read as "this assistant has none" for the moment
    // before the entries arrive, which is the opposite of the truth.
    expect(faqCountLabel("Knowledge", null)).toBe("Knowledge");
  });
});
