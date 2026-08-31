import { describe, expect, it } from "vitest";

import {
  canRecrawlKnowledgeSource,
  canRetryKnowledgeSource,
  knowledgeSourceDisplayName,
  knowledgeSourceTypeLabel,
  type KnowledgeSource,
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
