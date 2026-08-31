"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { useTenant } from "@/components/app/tenant-provider";
import {
  Button,
  Card,
  EmptyState,
  ErrorText,
  LoadingState,
  PageShell,
  Tabs,
} from "@/components/organizations/ui";
import {
  archiveAssistant,
  createAssistantVersion,
  diffAssistantVersions,
  getAssistant,
  listAssistantVersions,
  publishAssistant,
  renameAssistant,
  type Assistant,
  type AssistantVersion,
  type AssistantVersionDiff,
} from "@/lib/assistants";
import {
  createGlossaryEntry,
  deleteGlossaryEntry,
  listGlossaryEntries,
  updateGlossaryEntry,
  type GlossaryEntry,
} from "@/lib/glossary";
import {
  canRecrawlKnowledgeSource,
  canRetryKnowledgeSource,
  createFaqEntry,
  createManualFaqKnowledgeSource,
  createWebsiteKnowledgeSource,
  deleteFaqEntry,
  knowledgeSourceDisplayName,
  knowledgeSourceTypeLabel,
  listFaqEntries,
  listKnowledgeSources,
  processKnowledgeSource,
  recrawlKnowledgeSource,
  updateFaqEntry,
  uploadKnowledgeSourceFile,
  type FaqEntry,
  type KnowledgeSource,
} from "@/lib/knowledge-sources";
import {
  archivePromptTemplate,
  createPromptTemplate,
  createPromptTemplateVersion,
  diffPromptTemplateVersions,
  listPromptTemplates,
  listPromptTemplateVersions,
  publishPromptTemplate,
  renamePromptTemplate,
  type PromptTemplate,
  type PromptTemplateVersion,
  type PromptTemplateVersionDiff,
} from "@/lib/prompt-templates";
import { listVoices, type Voice } from "@/lib/voices";
import { COMMON_LOCALES } from "@/lib/workspaces";

const STATUS_TONE: Record<string, string> = {
  draft: "border-slate-700 text-slate-400",
  published: "border-green-800 text-green-300",
  archived: "border-slate-800 text-slate-500",
};

const EDITOR_TABS = [
  { key: "general", label: "General" },
  { key: "knowledge", label: "Knowledge" },
  { key: "customPrompt", label: "Custom Prompt" },
  { key: "technical", label: "Technical" },
] as const;

type EditorTabKey = (typeof EDITOR_TABS)[number]["key"];

function formatDiffValue(value: unknown): string {
  return value === null || value === undefined ? "(none)" : String(value);
}

function AssistantStatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${STATUS_TONE[status] ?? "border-slate-700 text-slate-400"}`}
    >
      {status}
    </span>
  );
}

const KNOWLEDGE_SOURCE_STATUS_TONE: Record<string, string> = {
  pending: "border-slate-700 text-slate-400",
  processing: "border-amber-800 text-amber-300",
  completed: "border-green-800 text-green-300",
  failed: "border-red-900 text-red-300",
};

function KnowledgeSourceStatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${KNOWLEDGE_SOURCE_STATUS_TONE[status] ?? "border-slate-700 text-slate-400"}`}
    >
      {status}
    </span>
  );
}

function PromptTemplateStatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${STATUS_TONE[status] ?? "border-slate-700 text-slate-400"}`}
    >
      {status}
    </span>
  );
}

export default function AssistantEditorPage() {
  const params = useParams<{ assistantId: string }>();
  const assistantId = params.assistantId;

  const {
    status: tenantStatus,
    error: tenantError,
    activeWorkspace,
  } = useTenant();

  const [assistant, setAssistant] = useState<Assistant | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [activeTab, setActiveTab] = useState<EditorTabKey>("general");

  const [name, setName] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const [voices, setVoices] = useState<Voice[] | null>(null);
  const [voicesError, setVoicesError] = useState<string | null>(null);

  const [voiceId, setVoiceId] = useState("");
  const [language, setLanguage] = useState(
    activeWorkspace?.settings.locale ?? "en-US",
  );
  const [greeting, setGreeting] = useState("");
  const [persona, setPersona] = useState("");
  const [speechRate, setSpeechRate] = useState(1);
  const [turnSensitivity, setTurnSensitivity] = useState(0.5);
  const [creativity, setCreativity] = useState(0.3);
  const [ambientSound, setAmbientSound] = useState("");
  const [ambientSoundVolume, setAmbientSoundVolume] = useState("");
  const [maxCallDurationSeconds, setMaxCallDurationSeconds] = useState("");
  const [maxSilenceTimeoutSeconds, setMaxSilenceTimeoutSeconds] = useState("");
  const [recordCalls, setRecordCalls] = useState(false);
  const [autoDeleteOnDeclinedConsent, setAutoDeleteOnDeclinedConsent] = useState(false);

  const [knowledgeSources, setKnowledgeSources] = useState<
    KnowledgeSource[] | null
  >(null);
  const [knowledgeSourcesError, setKnowledgeSourcesError] = useState<string | null>(
    null,
  );

  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [retryError, setRetryError] = useState<string | null>(null);

  const [websiteUrl, setWebsiteUrl] = useState("");
  const [addingWebsite, setAddingWebsite] = useState(false);
  const [addWebsiteError, setAddWebsiteError] = useState<string | null>(null);

  const [recrawlingId, setRecrawlingId] = useState<string | null>(null);
  const [recrawlError, setRecrawlError] = useState<string | null>(null);

  const [faqSourceName, setFaqSourceName] = useState("");
  const [addingFaqSource, setAddingFaqSource] = useState(false);
  const [addFaqSourceError, setAddFaqSourceError] = useState<string | null>(null);

  const [expandedFaqSourceId, setExpandedFaqSourceId] = useState<string | null>(null);
  const [faqEntries, setFaqEntries] = useState<FaqEntry[] | null>(null);
  const [faqEntriesError, setFaqEntriesError] = useState<string | null>(null);

  const [newFaqQuestion, setNewFaqQuestion] = useState("");
  const [newFaqAnswer, setNewFaqAnswer] = useState("");
  const [creatingFaqEntry, setCreatingFaqEntry] = useState(false);
  const [createFaqEntryError, setCreateFaqEntryError] = useState<string | null>(
    null,
  );

  const [editingFaqEntryId, setEditingFaqEntryId] = useState<string | null>(null);
  const [editFaqQuestion, setEditFaqQuestion] = useState("");
  const [editFaqAnswer, setEditFaqAnswer] = useState("");
  const [savingFaqEntryEdit, setSavingFaqEntryEdit] = useState(false);
  const [editFaqEntryError, setEditFaqEntryError] = useState<string | null>(null);

  const [deletingFaqEntryId, setDeletingFaqEntryId] = useState<string | null>(
    null,
  );
  const [deleteFaqEntryError, setDeleteFaqEntryError] = useState<string | null>(
    null,
  );

  const [promptTemplates, setPromptTemplates] = useState<PromptTemplate[] | null>(
    null,
  );
  const [promptTemplatesError, setPromptTemplatesError] = useState<string | null>(
    null,
  );

  const [newTemplateName, setNewTemplateName] = useState("");
  const [newTemplateUseCase, setNewTemplateUseCase] = useState("");
  const [creatingTemplate, setCreatingTemplate] = useState(false);
  const [createTemplateError, setCreateTemplateError] = useState<string | null>(
    null,
  );

  const [expandedTemplateId, setExpandedTemplateId] = useState<string | null>(
    null,
  );

  const [editTemplateName, setEditTemplateName] = useState("");
  const [renamingTemplate, setRenamingTemplate] = useState(false);
  const [renameTemplateError, setRenameTemplateError] = useState<string | null>(
    null,
  );

  const [archivingTemplateId, setArchivingTemplateId] = useState<string | null>(
    null,
  );
  const [archiveTemplateError, setArchiveTemplateError] = useState<string | null>(
    null,
  );

  const [templateContent, setTemplateContent] = useState("");
  const [savingTemplateVersion, setSavingTemplateVersion] = useState(false);
  const [saveTemplateVersionError, setSaveTemplateVersionError] = useState<
    string | null
  >(null);
  const [templateVersionSaved, setTemplateVersionSaved] =
    useState<PromptTemplateVersion | null>(null);

  const [templateVersions, setTemplateVersions] = useState<
    PromptTemplateVersion[] | null
  >(null);
  const [templateVersionsError, setTemplateVersionsError] = useState<
    string | null
  >(null);

  const [publishingTemplateVersion, setPublishingTemplateVersion] = useState<
    number | null
  >(null);
  const [publishTemplateVersionError, setPublishTemplateVersionError] =
    useState<string | null>(null);

  const [templateDiffFrom, setTemplateDiffFrom] = useState("");
  const [templateDiffTo, setTemplateDiffTo] = useState("");
  const [templateDiffResult, setTemplateDiffResult] =
    useState<PromptTemplateVersionDiff | null>(null);
  const [templateDiffError, setTemplateDiffError] = useState<string | null>(null);
  const [templateDiffLoading, setTemplateDiffLoading] = useState(false);

  const [selectedPromptTemplateId, setSelectedPromptTemplateId] = useState("");
  const [promptTemplateVersions, setPromptTemplateVersions] = useState<
    PromptTemplateVersion[] | null
  >(null);
  const [promptTemplateVersionsError, setPromptTemplateVersionsError] = useState<
    string | null
  >(null);
  const [selectedPromptVersion, setSelectedPromptVersion] = useState("");
  const [savingVersion, setSavingVersion] = useState(false);
  const [versionError, setVersionError] = useState<string | null>(null);
  const [versionSaved, setVersionSaved] = useState<AssistantVersion | null>(
    null,
  );

  const [versions, setVersions] = useState<AssistantVersion[] | null>(null);
  const [versionsError, setVersionsError] = useState<string | null>(null);
  const [publishingVersion, setPublishingVersion] = useState<number | null>(
    null,
  );
  const [publishError, setPublishError] = useState<string | null>(null);

  const [diffFrom, setDiffFrom] = useState("");
  const [diffTo, setDiffTo] = useState("");
  const [diffResult, setDiffResult] = useState<AssistantVersionDiff | null>(
    null,
  );
  const [diffError, setDiffError] = useState<string | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);

  const [glossaryEntries, setGlossaryEntries] = useState<GlossaryEntry[] | null>(
    null,
  );
  const [glossaryError, setGlossaryError] = useState<string | null>(null);

  const [newTerm, setNewTerm] = useState("");
  const [newMeaning, setNewMeaning] = useState("");
  const [newPhoneticSpelling, setNewPhoneticSpelling] = useState("");
  const [newBoostWeight, setNewBoostWeight] = useState(0.5);
  const [creatingEntry, setCreatingEntry] = useState(false);
  const [createEntryError, setCreateEntryError] = useState<string | null>(null);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTerm, setEditTerm] = useState("");
  const [editMeaning, setEditMeaning] = useState("");
  const [editPhoneticSpelling, setEditPhoneticSpelling] = useState("");
  const [editBoostWeight, setEditBoostWeight] = useState(0.5);
  const [savingEdit, setSavingEdit] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const fetchGlossaryEntries = useCallback(async () => {
    if (!activeWorkspace) {
      return null;
    }

    return listGlossaryEntries(
      activeWorkspace.organization_id,
      activeWorkspace.id,
      assistantId,
    );
  }, [activeWorkspace, assistantId]);

  const applyGlossaryEntries = useCallback(
    (loaded: GlossaryEntry[] | null) => {
      if (loaded) {
        setGlossaryEntries(loaded);
      }
    },
    [],
  );

  const applyGlossaryError = useCallback((err: unknown) => {
    setGlossaryError(
      err instanceof Error ? err.message : "Could not load the glossary.",
    );
  }, []);

  const loadGlossaryEntries = useCallback(async () => {
    try {
      applyGlossaryEntries(await fetchGlossaryEntries());
    } catch (err) {
      applyGlossaryError(err);
    }
  }, [fetchGlossaryEntries, applyGlossaryEntries, applyGlossaryError]);

  useEffect(() => {
    let cancelled = false;

    fetchGlossaryEntries()
      .then((loaded) => {
        if (!cancelled) {
          applyGlossaryEntries(loaded);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          applyGlossaryError(err);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [fetchGlossaryEntries, applyGlossaryEntries, applyGlossaryError]);

  const fetchVersions = useCallback(async () => {
    if (!activeWorkspace) {
      return null;
    }

    return listAssistantVersions(
      activeWorkspace.organization_id,
      activeWorkspace.id,
      assistantId,
    );
  }, [activeWorkspace, assistantId]);

  const applyVersions = useCallback((loaded: AssistantVersion[] | null) => {
    if (loaded) {
      setVersions(loaded);
    }
  }, []);

  const applyVersionsError = useCallback((err: unknown) => {
    setVersionsError(
      err instanceof Error ? err.message : "Could not load version history.",
    );
  }, []);

  const loadVersions = useCallback(async () => {
    try {
      applyVersions(await fetchVersions());
    } catch (err) {
      applyVersionsError(err);
    }
  }, [fetchVersions, applyVersions, applyVersionsError]);

  useEffect(() => {
    let cancelled = false;

    fetchVersions()
      .then((loaded) => {
        if (!cancelled) {
          applyVersions(loaded);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          applyVersionsError(err);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [fetchVersions, applyVersions, applyVersionsError]);

  useEffect(() => {
    let cancelled = false;

    listVoices()
      .then((loaded) => {
        if (!cancelled) {
          setVoices(loaded);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setVoicesError(
            err instanceof Error ? err.message : "Could not load voices.",
          );
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const fetchAssistant = useCallback(async () => {
    if (!activeWorkspace) {
      return null;
    }

    return getAssistant(
      activeWorkspace.organization_id,
      activeWorkspace.id,
      assistantId,
    );
  }, [activeWorkspace, assistantId]);

  const applyAssistant = useCallback((loaded: Assistant | null) => {
    if (loaded) {
      setAssistant(loaded);
      setName(loaded.name);
    }
  }, []);

  const applyLoadError = useCallback((err: unknown) => {
    setError(
      err instanceof Error ? err.message : "Could not load this assistant.",
    );
  }, []);

  const load = useCallback(async () => {
    try {
      applyAssistant(await fetchAssistant());
    } catch (err) {
      applyLoadError(err);
    }
  }, [fetchAssistant, applyAssistant, applyLoadError]);

  useEffect(() => {
    let cancelled = false;

    fetchAssistant()
      .then((loaded) => {
        if (!cancelled) {
          applyAssistant(loaded);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          applyLoadError(err);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [fetchAssistant, applyAssistant, applyLoadError]);

  // Fetched lazily - only once the Custom Prompt tab is actually viewed,
  // not on initial page load, matching how Technical's Glossary section is
  // also scoped to its own tab.
  useEffect(() => {
    if (activeTab !== "customPrompt" || !activeWorkspace || promptTemplates !== null) {
      return;
    }

    let cancelled = false;

    listPromptTemplates(activeWorkspace.organization_id, activeWorkspace.id)
      .then((loaded) => {
        if (!cancelled) {
          setPromptTemplates(loaded);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setPromptTemplatesError(
            err instanceof Error ? err.message : "Could not load prompt templates.",
          );
        }
      });

    return () => {
      cancelled = true;
    };
  }, [activeTab, activeWorkspace, promptTemplates]);

  async function refreshPromptTemplates() {
    if (!activeWorkspace) {
      return;
    }

    try {
      setPromptTemplates(
        await listPromptTemplates(activeWorkspace.organization_id, activeWorkspace.id),
      );
    } catch (err) {
      setPromptTemplatesError(
        err instanceof Error ? err.message : "Could not load prompt templates.",
      );
    }
  }

  async function handleCreateTemplate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!activeWorkspace) {
      return;
    }

    setCreateTemplateError(null);
    setCreatingTemplate(true);

    try {
      await createPromptTemplate(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        newTemplateName,
        newTemplateUseCase,
      );

      setNewTemplateName("");
      setNewTemplateUseCase("");
      await refreshPromptTemplates();
    } catch (err) {
      setCreateTemplateError(
        err instanceof Error ? err.message : "Could not create this prompt template.",
      );
    } finally {
      setCreatingTemplate(false);
    }
  }

  async function loadTemplateVersions(templateId: string) {
    if (!activeWorkspace) {
      return;
    }

    try {
      setTemplateVersions(
        await listPromptTemplateVersions(
          activeWorkspace.organization_id,
          activeWorkspace.id,
          templateId,
        ),
      );
    } catch (err) {
      setTemplateVersionsError(
        err instanceof Error ? err.message : "Could not load version history.",
      );
    }
  }

  function handleToggleTemplate(template: PromptTemplate) {
    if (expandedTemplateId === template.id) {
      setExpandedTemplateId(null);

      return;
    }

    setExpandedTemplateId(template.id);
    setEditTemplateName(template.name);
    setRenameTemplateError(null);
    setTemplateContent("");
    setTemplateVersionSaved(null);
    setSaveTemplateVersionError(null);
    setTemplateVersions(null);
    setTemplateVersionsError(null);
    setTemplateDiffFrom("");
    setTemplateDiffTo("");
    setTemplateDiffResult(null);
    setTemplateDiffError(null);
    loadTemplateVersions(template.id);
  }

  async function handleSaveTemplateVersion(
    event: FormEvent<HTMLFormElement>,
    templateId: string,
  ) {
    event.preventDefault();

    if (!activeWorkspace) {
      return;
    }

    setSaveTemplateVersionError(null);
    setTemplateVersionSaved(null);
    setSavingTemplateVersion(true);

    try {
      const created = await createPromptTemplateVersion(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        templateId,
        templateContent,
      );

      setTemplateVersionSaved(created);
      await loadTemplateVersions(templateId);
    } catch (err) {
      setSaveTemplateVersionError(
        err instanceof Error ? err.message : "Could not save this version.",
      );
    } finally {
      setSavingTemplateVersion(false);
    }
  }

  async function handlePublishTemplateVersion(templateId: string, version: number) {
    if (!activeWorkspace) {
      return;
    }

    setPublishTemplateVersionError(null);
    setPublishingTemplateVersion(version);

    try {
      await publishPromptTemplate(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        templateId,
        version,
      );
      await loadTemplateVersions(templateId);
      await refreshPromptTemplates();
    } catch (err) {
      setPublishTemplateVersionError(
        err instanceof Error ? err.message : "Could not publish this version.",
      );
    } finally {
      setPublishingTemplateVersion(null);
    }
  }

  async function handleDiffTemplateVersions(
    event: FormEvent<HTMLFormElement>,
    templateId: string,
  ) {
    event.preventDefault();

    if (!activeWorkspace) {
      return;
    }

    const from = Number(templateDiffFrom);
    const to = Number(templateDiffTo);

    if (!Number.isFinite(from) || !Number.isFinite(to)) {
      setTemplateDiffError("Choose two versions to compare.");

      return;
    }

    setTemplateDiffError(null);
    setTemplateDiffResult(null);
    setTemplateDiffLoading(true);

    try {
      const result = await diffPromptTemplateVersions(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        templateId,
        from,
        to,
      );

      setTemplateDiffResult(result);
    } catch (err) {
      setTemplateDiffError(
        err instanceof Error ? err.message : "Could not compare these versions.",
      );
    } finally {
      setTemplateDiffLoading(false);
    }
  }

  async function handleRenameTemplate(
    event: FormEvent<HTMLFormElement>,
    templateId: string,
  ) {
    event.preventDefault();

    if (!activeWorkspace) {
      return;
    }

    setRenameTemplateError(null);
    setRenamingTemplate(true);

    try {
      await renamePromptTemplate(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        templateId,
        editTemplateName,
      );
      await refreshPromptTemplates();
    } catch (err) {
      setRenameTemplateError(
        err instanceof Error ? err.message : "Could not rename this prompt template.",
      );
    } finally {
      setRenamingTemplate(false);
    }
  }

  async function handleArchiveTemplate(templateId: string) {
    if (!activeWorkspace) {
      return;
    }

    setArchiveTemplateError(null);
    setArchivingTemplateId(templateId);

    try {
      await archivePromptTemplate(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        templateId,
      );
      await refreshPromptTemplates();
    } catch (err) {
      setArchiveTemplateError(
        err instanceof Error ? err.message : "Could not archive this prompt template.",
      );
    } finally {
      setArchivingTemplateId(null);
    }
  }

  useEffect(() => {
    // No setState here when nothing is selected - the version section below
    // is already gated on selectedPromptTemplateId, so a stale non-null
    // promptTemplateVersions from a previous selection is never rendered.
    if (!activeWorkspace || !selectedPromptTemplateId) {
      return;
    }

    let cancelled = false;

    listPromptTemplateVersions(
      activeWorkspace.organization_id,
      activeWorkspace.id,
      selectedPromptTemplateId,
    )
      .then((loaded) => {
        if (!cancelled) {
          setPromptTemplateVersions(loaded);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setPromptTemplateVersionsError(
            err instanceof Error
              ? err.message
              : "Could not load this template's versions.",
          );
        }
      });

    return () => {
      cancelled = true;
    };
  }, [activeWorkspace, selectedPromptTemplateId]);

  // Fetched lazily - only once the Knowledge tab is actually viewed, matching
  // Custom Prompt's own lazy-fetch-on-tab-view pattern.
  useEffect(() => {
    if (activeTab !== "knowledge" || !activeWorkspace || knowledgeSources !== null) {
      return;
    }

    let cancelled = false;

    listKnowledgeSources(activeWorkspace.organization_id, activeWorkspace.id)
      .then((loaded) => {
        if (!cancelled) {
          setKnowledgeSources(loaded);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setKnowledgeSourcesError(
            err instanceof Error ? err.message : "Could not load knowledge sources.",
          );
        }
      });

    return () => {
      cancelled = true;
    };
  }, [activeTab, activeWorkspace, knowledgeSources]);

  async function refreshKnowledgeSources() {
    if (!activeWorkspace) {
      return;
    }

    try {
      setKnowledgeSources(
        await listKnowledgeSources(activeWorkspace.organization_id, activeWorkspace.id),
      );
    } catch (err) {
      setKnowledgeSourcesError(
        err instanceof Error ? err.message : "Could not load knowledge sources.",
      );
    }
  }

  async function handleUploadFile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!activeWorkspace || !uploadFile) {
      return;
    }

    setUploadError(null);
    setUploading(true);

    try {
      await uploadKnowledgeSourceFile(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        assistantId,
        uploadFile,
      );

      setUploadFile(null);
      await refreshKnowledgeSources();
    } catch (err) {
      setUploadError(
        err instanceof Error ? err.message : "Could not upload this file.",
      );
    } finally {
      setUploading(false);
    }
  }

  async function handleRetry(knowledgeSourceId: string) {
    if (!activeWorkspace) {
      return;
    }

    setRetryError(null);
    setRetryingId(knowledgeSourceId);

    try {
      await processKnowledgeSource(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        knowledgeSourceId,
      );
      await refreshKnowledgeSources();
    } catch (err) {
      setRetryError(
        err instanceof Error ? err.message : "Could not retry this source.",
      );
    } finally {
      setRetryingId(null);
    }
  }

  async function handleAddWebsite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!activeWorkspace || !websiteUrl.trim()) {
      return;
    }

    setAddWebsiteError(null);
    setAddingWebsite(true);

    try {
      await createWebsiteKnowledgeSource(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        assistantId,
        websiteUrl.trim(),
      );

      setWebsiteUrl("");
      await refreshKnowledgeSources();
    } catch (err) {
      setAddWebsiteError(
        err instanceof Error ? err.message : "Could not add this website.",
      );
    } finally {
      setAddingWebsite(false);
    }
  }

  async function handleRecrawl(knowledgeSourceId: string) {
    if (!activeWorkspace) {
      return;
    }

    setRecrawlError(null);
    setRecrawlingId(knowledgeSourceId);

    try {
      await recrawlKnowledgeSource(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        knowledgeSourceId,
      );
      await refreshKnowledgeSources();
    } catch (err) {
      setRecrawlError(
        err instanceof Error ? err.message : "Could not recrawl this source.",
      );
    } finally {
      setRecrawlingId(null);
    }
  }

  async function handleAddFaqSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!activeWorkspace || !faqSourceName.trim()) {
      return;
    }

    setAddFaqSourceError(null);
    setAddingFaqSource(true);

    try {
      await createManualFaqKnowledgeSource(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        assistantId,
        faqSourceName.trim(),
      );

      setFaqSourceName("");
      await refreshKnowledgeSources();
    } catch (err) {
      setAddFaqSourceError(
        err instanceof Error ? err.message : "Could not add this FAQ source.",
      );
    } finally {
      setAddingFaqSource(false);
    }
  }

  async function loadFaqEntries(knowledgeSourceId: string) {
    if (!activeWorkspace) {
      return;
    }

    try {
      setFaqEntries(
        await listFaqEntries(
          activeWorkspace.organization_id,
          activeWorkspace.id,
          knowledgeSourceId,
        ),
      );
    } catch (err) {
      setFaqEntriesError(
        err instanceof Error ? err.message : "Could not load this source's entries.",
      );
    }
  }

  function handleToggleFaqSource(knowledgeSourceId: string) {
    if (expandedFaqSourceId === knowledgeSourceId) {
      setExpandedFaqSourceId(null);

      return;
    }

    setExpandedFaqSourceId(knowledgeSourceId);
    setFaqEntries(null);
    setFaqEntriesError(null);
    setEditingFaqEntryId(null);
    loadFaqEntries(knowledgeSourceId);
  }

  async function handleCreateFaqEntry(
    event: FormEvent<HTMLFormElement>,
    knowledgeSourceId: string,
  ) {
    event.preventDefault();

    if (!activeWorkspace) {
      return;
    }

    setCreateFaqEntryError(null);
    setCreatingFaqEntry(true);

    try {
      await createFaqEntry(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        knowledgeSourceId,
        { question: newFaqQuestion, answer: newFaqAnswer },
      );

      setNewFaqQuestion("");
      setNewFaqAnswer("");
      await loadFaqEntries(knowledgeSourceId);
    } catch (err) {
      setCreateFaqEntryError(
        err instanceof Error ? err.message : "Could not add this entry.",
      );
    } finally {
      setCreatingFaqEntry(false);
    }
  }

  function handleStartEditFaqEntry(entry: FaqEntry) {
    setEditingFaqEntryId(entry.id);
    setEditFaqQuestion(entry.question);
    setEditFaqAnswer(entry.answer);
    setEditFaqEntryError(null);
  }

  function handleCancelEditFaqEntry() {
    setEditingFaqEntryId(null);
    setEditFaqEntryError(null);
  }

  async function handleSaveFaqEntryEdit(
    knowledgeSourceId: string,
    faqEntryId: string,
  ) {
    if (!activeWorkspace) {
      return;
    }

    setEditFaqEntryError(null);
    setSavingFaqEntryEdit(true);

    try {
      await updateFaqEntry(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        knowledgeSourceId,
        faqEntryId,
        { question: editFaqQuestion, answer: editFaqAnswer },
      );

      setEditingFaqEntryId(null);
      await loadFaqEntries(knowledgeSourceId);
    } catch (err) {
      setEditFaqEntryError(
        err instanceof Error ? err.message : "Could not save this entry.",
      );
    } finally {
      setSavingFaqEntryEdit(false);
    }
  }

  async function handleDeleteFaqEntry(
    knowledgeSourceId: string,
    faqEntryId: string,
  ) {
    if (!activeWorkspace) {
      return;
    }

    setDeleteFaqEntryError(null);
    setDeletingFaqEntryId(faqEntryId);

    try {
      await deleteFaqEntry(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        knowledgeSourceId,
        faqEntryId,
      );
      await loadFaqEntries(knowledgeSourceId);
    } catch (err) {
      setDeleteFaqEntryError(
        err instanceof Error ? err.message : "Could not delete this entry.",
      );
    } finally {
      setDeletingFaqEntryId(null);
    }
  }

  async function handleRename(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!activeWorkspace) {
      return;
    }

    setActionError(null);
    setRenaming(true);

    try {
      await renameAssistant(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        assistantId,
        name,
      );
      await load();
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Could not rename this assistant.",
      );
    } finally {
      setRenaming(false);
    }
  }

  async function handleArchive() {
    if (!activeWorkspace) {
      return;
    }

    setActionError(null);
    setArchiving(true);

    try {
      await archiveAssistant(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        assistantId,
      );
      await load();
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Could not archive this assistant.",
      );
    } finally {
      setArchiving(false);
    }
  }

  async function handleSaveVersion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!activeWorkspace) {
      return;
    }

    const parsedMaxCallDuration = maxCallDurationSeconds.trim()
      ? Number(maxCallDurationSeconds)
      : null;
    const parsedMaxSilenceTimeout = maxSilenceTimeoutSeconds.trim()
      ? Number(maxSilenceTimeoutSeconds)
      : null;
    const parsedAmbientSoundVolume = ambientSoundVolume.trim()
      ? Number(ambientSoundVolume)
      : null;

    if (
      !Number.isFinite(speechRate) ||
      !Number.isFinite(turnSensitivity) ||
      !Number.isFinite(creativity) ||
      (parsedMaxCallDuration !== null && !Number.isFinite(parsedMaxCallDuration)) ||
      (parsedMaxSilenceTimeout !== null && !Number.isFinite(parsedMaxSilenceTimeout)) ||
      (parsedAmbientSoundVolume !== null && !Number.isFinite(parsedAmbientSoundVolume))
    ) {
      setVersionError(
        "Speech rate, turn sensitivity, creativity, and any call/ambient-sound numbers must be valid.",
      );

      return;
    }

    setVersionError(null);
    setVersionSaved(null);
    setSavingVersion(true);

    try {
      const created = await createAssistantVersion(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        assistantId,
        {
          voice_id: voiceId,
          language,
          greeting,
          persona: persona.trim() ? persona : null,
          speech_rate: speechRate,
          turn_sensitivity: turnSensitivity,
          creativity,
          ambient_sound: ambientSound.trim() ? ambientSound : null,
          ambient_sound_volume: parsedAmbientSoundVolume,
          max_call_duration_seconds: parsedMaxCallDuration,
          max_silence_timeout_seconds: parsedMaxSilenceTimeout,
          record_calls: recordCalls,
          auto_delete_on_declined_consent: autoDeleteOnDeclinedConsent,
          prompt_template_id: selectedPromptTemplateId || null,
          prompt_version: selectedPromptTemplateId
            ? Number(selectedPromptVersion)
            : null,
        },
      );

      setVersionSaved(created);
      await loadVersions();
    } catch (err) {
      setVersionError(
        err instanceof Error ? err.message : "Could not save this version.",
      );
    } finally {
      setSavingVersion(false);
    }
  }

  async function handlePublish(version: number) {
    if (!activeWorkspace) {
      return;
    }

    setPublishError(null);
    setPublishingVersion(version);

    try {
      await publishAssistant(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        assistantId,
        version,
      );
      await load();
    } catch (err) {
      setPublishError(
        err instanceof Error ? err.message : "Could not publish this version.",
      );
    } finally {
      setPublishingVersion(null);
    }
  }

  async function handleDiff(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!activeWorkspace) {
      return;
    }

    const from = Number(diffFrom);
    const to = Number(diffTo);

    if (!Number.isFinite(from) || !Number.isFinite(to)) {
      setDiffError("Choose two versions to compare.");

      return;
    }

    setDiffError(null);
    setDiffResult(null);
    setDiffLoading(true);

    try {
      const result = await diffAssistantVersions(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        assistantId,
        from,
        to,
      );

      setDiffResult(result);
    } catch (err) {
      setDiffError(
        err instanceof Error ? err.message : "Could not compare these versions.",
      );
    } finally {
      setDiffLoading(false);
    }
  }

  async function handleCreateEntry(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!activeWorkspace) {
      return;
    }

    setCreateEntryError(null);
    setCreatingEntry(true);

    try {
      await createGlossaryEntry(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        assistantId,
        {
          term: newTerm,
          meaning: newMeaning.trim() ? newMeaning : null,
          phonetic_spelling: newPhoneticSpelling.trim() ? newPhoneticSpelling : null,
          stt_boost_weight: newBoostWeight,
        },
      );

      setNewTerm("");
      setNewMeaning("");
      setNewPhoneticSpelling("");
      setNewBoostWeight(0.5);
      await loadGlossaryEntries();
    } catch (err) {
      setCreateEntryError(
        err instanceof Error ? err.message : "Could not add this glossary entry.",
      );
    } finally {
      setCreatingEntry(false);
    }
  }

  function handleStartEdit(entry: GlossaryEntry) {
    setEditingId(entry.id);
    setEditTerm(entry.term);
    setEditMeaning(entry.meaning ?? "");
    setEditPhoneticSpelling(entry.phonetic_spelling ?? "");
    setEditBoostWeight(entry.stt_boost_weight);
    setEditError(null);
  }

  function handleCancelEdit() {
    setEditingId(null);
    setEditError(null);
  }

  async function handleSaveEdit(glossaryEntryId: string) {
    if (!activeWorkspace) {
      return;
    }

    setEditError(null);
    setSavingEdit(true);

    try {
      await updateGlossaryEntry(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        assistantId,
        glossaryEntryId,
        {
          term: editTerm,
          meaning: editMeaning.trim() ? editMeaning : null,
          phonetic_spelling: editPhoneticSpelling.trim() ? editPhoneticSpelling : null,
          stt_boost_weight: editBoostWeight,
        },
      );

      setEditingId(null);
      await loadGlossaryEntries();
    } catch (err) {
      setEditError(
        err instanceof Error ? err.message : "Could not save this glossary entry.",
      );
    } finally {
      setSavingEdit(false);
    }
  }

  async function handleDeleteEntry(glossaryEntryId: string) {
    if (!activeWorkspace) {
      return;
    }

    setDeleteError(null);
    setDeletingId(glossaryEntryId);

    try {
      await deleteGlossaryEntry(
        activeWorkspace.organization_id,
        activeWorkspace.id,
        assistantId,
        glossaryEntryId,
      );
      await loadGlossaryEntries();
    } catch (err) {
      setDeleteError(
        err instanceof Error ? err.message : "Could not delete this glossary entry.",
      );
    } finally {
      setDeletingId(null);
    }
  }

  if (tenantStatus === "error") {
    return (
      <PageShell title="Assistant">
        <ErrorText message={tenantError ?? "Could not load your workspace."} />
      </PageShell>
    );
  }

  if (tenantStatus === "loading" || (!error && !assistant)) {
    return (
      <PageShell title="Assistant">
        <LoadingState />
      </PageShell>
    );
  }

  if (error) {
    return (
      <PageShell title="Assistant">
        <ErrorText message={error} />
      </PageShell>
    );
  }

  if (!assistant) {
    return null;
  }

  const archived = assistant.status === "archived";

  return (
    <PageShell title={assistant.name} description="Assistant configuration.">
      <div className="mb-6 flex items-center justify-between gap-4">
        <Link href="/assistants" className="text-sm text-slate-400 hover:text-slate-300">
          &larr; Back to assistants
        </Link>

        <Link
          href={`/assistants/${assistant.id}/test-call`}
          className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-900"
        >
          Test call
        </Link>
      </div>

      {actionError && (
        <div className="mb-4">
          <ErrorText message={actionError} />
        </div>
      )}

      <Card>
        <div className="mb-4 flex items-center gap-3">
          <h2 className="text-lg font-semibold">Identity</h2>
          <AssistantStatusBadge status={assistant.status} />
        </div>

        <form onSubmit={handleRename} className="flex flex-wrap gap-3">
          <input
            aria-label="Assistant name"
            className="min-w-64 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
            required
            disabled={renaming || archived}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />

          <Button
            type="submit"
            disabled={renaming || archived || !name.trim()}
          >
            {renaming ? "Saving..." : "Save name"}
          </Button>
        </form>

        <div className="mt-6 border-t border-slate-800 pt-4">
          {archived ? (
            <p className="text-sm text-slate-500">This assistant is archived.</p>
          ) : (
            <Button
              variant="secondary"
              disabled={archiving}
              onClick={handleArchive}
            >
              {archiving ? "Archiving..." : "Archive assistant"}
            </Button>
          )}
        </div>
      </Card>

      <div className="mt-8">
        <Tabs items={EDITOR_TABS} activeKey={activeTab} onChange={setActiveTab} />
      </div>

      {activeTab === "general" && (
      <div className="mt-6">
        <Card>
          <h2 className="text-lg font-semibold">Configuration</h2>
          <p className="mt-1 text-sm text-slate-400">
            Saving posts a full new version snapshot - versions are immutable.
          </p>

          {voicesError && (
            <div className="mt-4">
              <ErrorText message={voicesError} />
            </div>
          )}

          {voices === null && !voicesError && (
            <div className="mt-4">
              <LoadingState message="Loading voices..." />
            </div>
          )}

          {voices !== null && voices.length === 0 && (
            <div className="mt-4">
              <EmptyState message="No voices are available yet. Add one to the voice catalogue first." />
            </div>
          )}

          {voices !== null && voices.length > 0 && (
            <form
              onSubmit={handleSaveVersion}
              className="mt-4 space-y-6"
              noValidate
            >
              {versionError && <ErrorText message={versionError} />}
              {versionSaved && (
                <p className="text-sm text-green-400">
                  Saved as version {versionSaved.version}.
                </p>
              )}

              <div>
                <label
                  htmlFor="voice_id"
                  className="block text-sm font-medium text-slate-200"
                >
                  Voice
                </label>

                <select
                  id="voice_id"
                  className="mt-2 w-full max-w-md rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                  disabled={savingVersion}
                  value={voiceId}
                  onChange={(event) => setVoiceId(event.target.value)}
                >
                  <option value="" disabled>
                    Select a voice
                  </option>
                  {voices.map((voice) => (
                    <option key={voice.id} value={voice.id}>
                      {voice.name} ({voice.language})
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label
                  htmlFor="language"
                  className="block text-sm font-medium text-slate-200"
                >
                  Language
                </label>

                <select
                  id="language"
                  className="mt-2 w-full max-w-md rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                  disabled={savingVersion}
                  value={language}
                  onChange={(event) => setLanguage(event.target.value)}
                >
                  {COMMON_LOCALES.map((locale) => (
                    <option key={locale} value={locale}>
                      {locale}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label
                  htmlFor="greeting"
                  className="block text-sm font-medium text-slate-200"
                >
                  Greeting
                </label>

                <textarea
                  id="greeting"
                  rows={2}
                  maxLength={2000}
                  required
                  disabled={savingVersion}
                  className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                  placeholder="Thanks for calling - how can I help?"
                  value={greeting}
                  onChange={(event) => setGreeting(event.target.value)}
                />
              </div>

              <div>
                <label
                  htmlFor="persona"
                  className="block text-sm font-medium text-slate-200"
                >
                  Persona
                </label>

                <textarea
                  id="persona"
                  rows={4}
                  maxLength={4000}
                  disabled={savingVersion}
                  className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                  placeholder="Optional - behavioral instructions for this assistant."
                  value={persona}
                  onChange={(event) => setPersona(event.target.value)}
                />
              </div>

              <Button
                type="submit"
                disabled={
                  savingVersion ||
                  !voiceId ||
                  !greeting.trim() ||
                  (!!selectedPromptTemplateId && !selectedPromptVersion)
                }
              >
                {savingVersion ? "Saving..." : "Save as new version"}
              </Button>
            </form>
          )}
        </Card>
      </div>
      )}

      {activeTab === "knowledge" && (
        <div className="mt-6">
          <Card>
            <h2 className="text-lg font-semibold">Knowledge</h2>
            <p className="mt-1 text-sm text-slate-400">
              Files, websites, and manual FAQs this assistant can answer from.
            </p>

            <form
              onSubmit={handleUploadFile}
              className="mt-4 flex flex-wrap items-center gap-3 border-b border-slate-800 pb-4"
              noValidate
            >
              <input
                aria-label="Upload a file"
                type="file"
                accept=".pdf,.docx,.md,.txt"
                disabled={uploading}
                onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)}
                className="text-sm text-slate-300 file:mr-3 file:rounded-lg file:border file:border-slate-700 file:bg-slate-950 file:px-3 file:py-2 file:text-sm file:text-white"
              />
              <Button type="submit" disabled={uploading || !uploadFile}>
                {uploading ? "Uploading..." : "Upload file"}
              </Button>
              {uploadError && <ErrorText message={uploadError} />}
            </form>

            <form
              onSubmit={handleAddWebsite}
              className="mt-4 flex flex-wrap items-center gap-3 border-b border-slate-800 pb-4"
              noValidate
            >
              <input
                aria-label="Website URL"
                type="url"
                placeholder="https://example.com"
                required
                disabled={addingWebsite}
                value={websiteUrl}
                onChange={(event) => setWebsiteUrl(event.target.value)}
                className="min-w-64 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
              />
              <Button type="submit" disabled={addingWebsite || !websiteUrl.trim()}>
                {addingWebsite ? "Adding..." : "Add website"}
              </Button>
              {addWebsiteError && <ErrorText message={addWebsiteError} />}
            </form>

            <form
              onSubmit={handleAddFaqSource}
              className="mt-4 flex flex-wrap items-center gap-3 border-b border-slate-800 pb-4"
              noValidate
            >
              <input
                aria-label="FAQ source name"
                placeholder="FAQ source name"
                required
                disabled={addingFaqSource}
                value={faqSourceName}
                onChange={(event) => setFaqSourceName(event.target.value)}
                className="min-w-64 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
              />
              <Button type="submit" disabled={addingFaqSource || !faqSourceName.trim()}>
                {addingFaqSource ? "Adding..." : "Add FAQ source"}
              </Button>
              {addFaqSourceError && <ErrorText message={addFaqSourceError} />}
            </form>

            {retryError && (
              <div className="mt-4">
                <ErrorText message={retryError} />
              </div>
            )}

            {recrawlError && (
              <div className="mt-4">
                <ErrorText message={recrawlError} />
              </div>
            )}

            {knowledgeSourcesError && (
              <div className="mt-4">
                <ErrorText message={knowledgeSourcesError} />
              </div>
            )}

            {knowledgeSources === null && !knowledgeSourcesError && (
              <div className="mt-4">
                <LoadingState message="Loading knowledge sources..." />
              </div>
            )}

            {knowledgeSources !== null && knowledgeSources.length === 0 && (
              <div className="mt-4">
                <EmptyState message="No knowledge sources yet." />
              </div>
            )}

            {knowledgeSources !== null && knowledgeSources.length > 0 && (
              <ul className="mt-4 space-y-3">
                {knowledgeSources.map((source) => (
                  <li
                    key={source.id}
                    className="rounded-xl border border-slate-800 px-4 py-3"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <span className="text-xs font-medium text-slate-500">
                          {knowledgeSourceTypeLabel(source.type)}
                        </span>
                        <span className="ml-3 font-medium">
                          {knowledgeSourceDisplayName(source)}
                        </span>
                      </div>

                      <KnowledgeSourceStatusBadge status={source.status} />
                    </div>

                    {source.type === "website" && source.crawled_pages && (
                      <p className="mt-1 text-xs text-slate-500">
                        {source.crawled_pages.length} page
                        {source.crawled_pages.length === 1 ? "" : "s"} crawled
                      </p>
                    )}

                    {source.status === "failed" && source.error_message && (
                      <div className="mt-2">
                        <ErrorText message={source.error_message} />
                      </div>
                    )}

                    <div className="mt-2 flex gap-2">
                      {canRetryKnowledgeSource(source) && (
                        <Button
                          variant="secondary"
                          disabled={retryingId === source.id}
                          onClick={() => handleRetry(source.id)}
                        >
                          {retryingId === source.id ? "Retrying..." : "Retry"}
                        </Button>
                      )}

                      {canRecrawlKnowledgeSource(source) && (
                        <Button
                          variant="secondary"
                          disabled={recrawlingId === source.id}
                          onClick={() => handleRecrawl(source.id)}
                        >
                          {recrawlingId === source.id ? "Recrawling..." : "Recrawl"}
                        </Button>
                      )}

                      {source.type === "manual_faq" && (
                        <Button
                          variant="secondary"
                          onClick={() => handleToggleFaqSource(source.id)}
                        >
                          {expandedFaqSourceId === source.id
                            ? "Hide entries"
                            : "Manage entries"}
                        </Button>
                      )}
                    </div>

                    {source.type === "manual_faq" &&
                      expandedFaqSourceId === source.id && (
                        <div className="mt-4 border-t border-slate-800 pt-4">
                          {faqEntriesError && (
                            <div className="mb-3">
                              <ErrorText message={faqEntriesError} />
                            </div>
                          )}

                          {faqEntries === null && !faqEntriesError && (
                            <LoadingState message="Loading entries..." />
                          )}

                          {faqEntries !== null && faqEntries.length === 0 && (
                            <EmptyState message="No entries yet." />
                          )}

                          {faqEntries !== null && faqEntries.length > 0 && (
                            <ul className="space-y-3">
                              {faqEntries.map((entry) =>
                                editingFaqEntryId === entry.id ? (
                                  <li
                                    key={entry.id}
                                    className="rounded-xl border border-slate-800 px-4 py-3"
                                  >
                                    {editFaqEntryError && (
                                      <div className="mb-3">
                                        <ErrorText message={editFaqEntryError} />
                                      </div>
                                    )}

                                    <div className="space-y-2">
                                      <input
                                        aria-label="Edit question"
                                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                                        placeholder="Question"
                                        disabled={savingFaqEntryEdit}
                                        value={editFaqQuestion}
                                        onChange={(event) =>
                                          setEditFaqQuestion(event.target.value)
                                        }
                                      />
                                      <input
                                        aria-label="Edit answer"
                                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                                        placeholder="Answer"
                                        disabled={savingFaqEntryEdit}
                                        value={editFaqAnswer}
                                        onChange={(event) =>
                                          setEditFaqAnswer(event.target.value)
                                        }
                                      />
                                    </div>

                                    <div className="mt-3 flex gap-3">
                                      <Button
                                        disabled={
                                          savingFaqEntryEdit ||
                                          !editFaqQuestion.trim() ||
                                          !editFaqAnswer.trim()
                                        }
                                        onClick={() =>
                                          handleSaveFaqEntryEdit(source.id, entry.id)
                                        }
                                      >
                                        {savingFaqEntryEdit ? "Saving..." : "Save"}
                                      </Button>
                                      <Button
                                        variant="secondary"
                                        disabled={savingFaqEntryEdit}
                                        onClick={handleCancelEditFaqEntry}
                                      >
                                        Cancel
                                      </Button>
                                    </div>
                                  </li>
                                ) : (
                                  <li
                                    key={entry.id}
                                    className="rounded-xl border border-slate-800 px-4 py-3"
                                  >
                                    <p className="font-medium">{entry.question}</p>
                                    <p className="mt-1 text-sm text-slate-400">
                                      {entry.answer}
                                    </p>

                                    <div className="mt-3 flex gap-2">
                                      <Button
                                        variant="secondary"
                                        disabled={deletingFaqEntryId === entry.id}
                                        onClick={() => handleStartEditFaqEntry(entry)}
                                      >
                                        Edit
                                      </Button>
                                      <Button
                                        variant="secondary"
                                        disabled={deletingFaqEntryId === entry.id}
                                        onClick={() =>
                                          handleDeleteFaqEntry(source.id, entry.id)
                                        }
                                      >
                                        {deletingFaqEntryId === entry.id
                                          ? "Deleting..."
                                          : "Delete"}
                                      </Button>
                                    </div>
                                  </li>
                                ),
                              )}
                            </ul>
                          )}

                          {deleteFaqEntryError && (
                            <div className="mt-3">
                              <ErrorText message={deleteFaqEntryError} />
                            </div>
                          )}

                          <form
                            onSubmit={(event) =>
                              handleCreateFaqEntry(event, source.id)
                            }
                            className="mt-4 space-y-3"
                            noValidate
                          >
                            <h3 className="text-sm font-semibold text-slate-300">
                              Add entry
                            </h3>

                            {createFaqEntryError && (
                              <ErrorText message={createFaqEntryError} />
                            )}

                            <input
                              aria-label="Question"
                              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                              placeholder="Question"
                              required
                              disabled={creatingFaqEntry}
                              value={newFaqQuestion}
                              onChange={(event) =>
                                setNewFaqQuestion(event.target.value)
                              }
                            />
                            <input
                              aria-label="Answer"
                              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                              placeholder="Answer"
                              required
                              disabled={creatingFaqEntry}
                              value={newFaqAnswer}
                              onChange={(event) => setNewFaqAnswer(event.target.value)}
                            />
                            <Button
                              type="submit"
                              disabled={
                                creatingFaqEntry ||
                                !newFaqQuestion.trim() ||
                                !newFaqAnswer.trim()
                              }
                            >
                              {creatingFaqEntry ? "Adding..." : "Add entry"}
                            </Button>
                          </form>
                        </div>
                      )}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      )}

      {activeTab === "customPrompt" && (
        <>
        <div className="mt-6">
          <Card>
            <h2 className="text-lg font-semibold">Custom Prompt</h2>
            <p className="mt-1 text-sm text-slate-400">
              Pick a reusable prompt template and a specific version - saved as part of this
              assistant&apos;s version snapshot.
            </p>

            {promptTemplatesError && (
              <div className="mt-4">
                <ErrorText message={promptTemplatesError} />
              </div>
            )}

            {promptTemplates === null && !promptTemplatesError && (
              <div className="mt-4">
                <LoadingState message="Loading prompt templates..." />
              </div>
            )}

            {promptTemplates !== null && promptTemplates.length === 0 && (
              <div className="mt-4">
                <EmptyState message="No prompt templates in this workspace yet." />
              </div>
            )}

            {promptTemplates !== null && promptTemplates.length > 0 && (
              <form onSubmit={handleSaveVersion} className="mt-4 space-y-4" noValidate>
                {versionError && <ErrorText message={versionError} />}
                {versionSaved && (
                  <p className="text-sm text-green-400">
                    Saved as version {versionSaved.version}.
                  </p>
                )}

                <div>
                  <label
                    htmlFor="prompt_template"
                    className="block text-sm font-medium text-slate-200"
                  >
                    Prompt template
                  </label>

                  <select
                    id="prompt_template"
                    disabled={savingVersion}
                    className="mt-2 w-full max-w-md rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                    value={selectedPromptTemplateId}
                    onChange={(event) => {
                      setSelectedPromptTemplateId(event.target.value);
                      setSelectedPromptVersion("");
                    }}
                  >
                    <option value="">None</option>
                    {promptTemplates.map((template) => (
                      <option key={template.id} value={template.id}>
                        {template.name} ({template.use_case})
                      </option>
                    ))}
                  </select>
                </div>

                {selectedPromptTemplateId && (
                  <div>
                    <label
                      htmlFor="prompt_version"
                      className="block text-sm font-medium text-slate-200"
                    >
                      Version
                    </label>

                    {promptTemplateVersionsError && (
                      <div className="mt-2">
                        <ErrorText message={promptTemplateVersionsError} />
                      </div>
                    )}

                    {promptTemplateVersions === null && !promptTemplateVersionsError && (
                      <div className="mt-2">
                        <LoadingState message="Loading versions..." />
                      </div>
                    )}

                    {promptTemplateVersions !== null &&
                      promptTemplateVersions.length === 0 && (
                        <div className="mt-2">
                          <EmptyState message="This template has no saved versions yet." />
                        </div>
                      )}

                    {promptTemplateVersions !== null &&
                      promptTemplateVersions.length > 0 && (
                        <select
                          id="prompt_version"
                          disabled={savingVersion}
                          className="mt-2 w-full max-w-xs rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                          value={selectedPromptVersion}
                          onChange={(event) =>
                            setSelectedPromptVersion(event.target.value)
                          }
                        >
                          <option value="" disabled>
                            Select a version
                          </option>
                          {promptTemplateVersions.map((version) => (
                            <option key={version.id} value={version.version}>
                              Version {version.version}
                            </option>
                          ))}
                        </select>
                      )}

                    {selectedPromptVersion && promptTemplateVersions && (
                      <div className="mt-3">
                        <p className="text-xs font-medium text-slate-400">Preview</p>
                        <pre className="mt-1 max-w-2xl whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-sm text-slate-300">
                          {
                            promptTemplateVersions.find(
                              (version) =>
                                version.version === Number(selectedPromptVersion),
                            )?.content
                          }
                        </pre>
                      </div>
                    )}
                  </div>
                )}

                <Button
                  type="submit"
                  disabled={
                    savingVersion ||
                    !voiceId ||
                    !greeting.trim() ||
                    (!!selectedPromptTemplateId && !selectedPromptVersion)
                  }
                >
                  {savingVersion ? "Saving..." : "Save as new version"}
                </Button>
              </form>
            )}
          </Card>
        </div>

        <div className="mt-6">
          <Card>
            <h2 className="text-lg font-semibold">Manage templates</h2>
            <p className="mt-1 text-sm text-slate-400">
              Prompt templates are shared across every assistant in this workspace.
            </p>

            <form
              onSubmit={handleCreateTemplate}
              className="mt-4 flex flex-wrap items-center gap-3 border-b border-slate-800 pb-4"
              noValidate
            >
              <input
                aria-label="New template name"
                placeholder="Front Desk Receptionist"
                required
                disabled={creatingTemplate}
                value={newTemplateName}
                onChange={(event) => setNewTemplateName(event.target.value)}
                className="min-w-56 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
              />
              <input
                aria-label="New template use case"
                placeholder="receptionist"
                required
                disabled={creatingTemplate}
                value={newTemplateUseCase}
                onChange={(event) => setNewTemplateUseCase(event.target.value)}
                className="min-w-40 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
              />
              <Button
                type="submit"
                disabled={
                  creatingTemplate || !newTemplateName.trim() || !newTemplateUseCase.trim()
                }
              >
                {creatingTemplate ? "Creating..." : "Create template"}
              </Button>
              {createTemplateError && <ErrorText message={createTemplateError} />}
            </form>

            {archiveTemplateError && (
              <div className="mt-4">
                <ErrorText message={archiveTemplateError} />
              </div>
            )}

            {promptTemplatesError && (
              <div className="mt-4">
                <ErrorText message={promptTemplatesError} />
              </div>
            )}

            {promptTemplates === null && !promptTemplatesError && (
              <div className="mt-4">
                <LoadingState message="Loading prompt templates..." />
              </div>
            )}

            {promptTemplates !== null && promptTemplates.length === 0 && (
              <div className="mt-4">
                <EmptyState message="No prompt templates yet. Create one above to get started." />
              </div>
            )}

            {promptTemplates !== null && promptTemplates.length > 0 && (
              <ul className="mt-4 space-y-3">
                {promptTemplates.map((template) => (
                  <li
                    key={template.id}
                    className="rounded-xl border border-slate-800 px-4 py-3"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <span className="font-medium">{template.name}</span>
                        <span className="ml-3 text-sm text-slate-500">
                          {template.use_case}
                        </span>
                      </div>

                      <div className="flex items-center gap-3">
                        <PromptTemplateStatusBadge status={template.status} />
                        <Button
                          variant="secondary"
                          onClick={() => handleToggleTemplate(template)}
                        >
                          {expandedTemplateId === template.id ? "Hide" : "Manage"}
                        </Button>
                      </div>
                    </div>

                    {expandedTemplateId === template.id && (
                      <div className="mt-4 space-y-4 border-t border-slate-800 pt-4">
                        {renameTemplateError && (
                          <ErrorText message={renameTemplateError} />
                        )}

                        <form
                          onSubmit={(event) => handleRenameTemplate(event, template.id)}
                          className="flex flex-wrap gap-3"
                        >
                          <input
                            aria-label="Edit template name"
                            required
                            disabled={
                              renamingTemplate || template.status === "archived"
                            }
                            value={editTemplateName}
                            onChange={(event) =>
                              setEditTemplateName(event.target.value)
                            }
                            className="min-w-56 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                          />
                          <Button
                            type="submit"
                            disabled={
                              renamingTemplate ||
                              template.status === "archived" ||
                              !editTemplateName.trim()
                            }
                          >
                            {renamingTemplate ? "Saving..." : "Save name"}
                          </Button>
                        </form>

                        {template.status === "archived" ? (
                          <p className="text-sm text-slate-500">
                            This prompt template is archived.
                          </p>
                        ) : (
                          <Button
                            variant="secondary"
                            disabled={archivingTemplateId === template.id}
                            onClick={() => handleArchiveTemplate(template.id)}
                          >
                            {archivingTemplateId === template.id
                              ? "Archiving..."
                              : "Archive prompt template"}
                          </Button>
                        )}

                        <div className="border-t border-slate-800 pt-4">
                          <h3 className="text-sm font-semibold text-slate-300">
                            Content
                          </h3>
                          <p className="mt-1 text-xs text-slate-500">
                            Saving posts a full new version snapshot - versions are
                            immutable.
                          </p>

                          <form
                            onSubmit={(event) =>
                              handleSaveTemplateVersion(event, template.id)
                            }
                            className="mt-3 space-y-3"
                            noValidate
                          >
                            {saveTemplateVersionError && (
                              <ErrorText message={saveTemplateVersionError} />
                            )}
                            {templateVersionSaved && (
                              <p className="text-sm text-green-400">
                                Saved as version {templateVersionSaved.version}.
                              </p>
                            )}

                            <textarea
                              aria-label="Template content"
                              rows={5}
                              maxLength={20000}
                              required
                              disabled={savingTemplateVersion}
                              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                              placeholder="Thanks for calling {{workspace.name}} - how can I help?"
                              value={templateContent}
                              onChange={(event) =>
                                setTemplateContent(event.target.value)
                              }
                            />

                            <Button
                              type="submit"
                              disabled={
                                savingTemplateVersion || !templateContent.trim()
                              }
                            >
                              {savingTemplateVersion
                                ? "Saving..."
                                : "Save as new version"}
                            </Button>
                          </form>
                        </div>

                        <div className="border-t border-slate-800 pt-4">
                          <h3 className="text-sm font-semibold text-slate-300">
                            Version history
                          </h3>

                          {publishTemplateVersionError && (
                            <div className="mt-3">
                              <ErrorText message={publishTemplateVersionError} />
                            </div>
                          )}

                          {templateVersionsError && (
                            <div className="mt-3">
                              <ErrorText message={templateVersionsError} />
                            </div>
                          )}

                          {templateVersions === null && !templateVersionsError && (
                            <div className="mt-3">
                              <LoadingState message="Loading versions..." />
                            </div>
                          )}

                          {templateVersions !== null &&
                            templateVersions.length === 0 && (
                              <div className="mt-3">
                                <EmptyState message="This template has no saved versions yet." />
                              </div>
                            )}

                          {templateVersions !== null &&
                            templateVersions.length > 0 && (
                              <ul className="mt-3 space-y-2">
                                {templateVersions.map((version) => {
                                  const isCurrent =
                                    version.id === template.current_version_id;

                                  return (
                                    <li
                                      key={version.id}
                                      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-800 px-3 py-2"
                                    >
                                      <div>
                                        <span className="font-medium">
                                          Version {version.version}
                                        </span>
                                        <span className="ml-3 text-sm text-slate-500">
                                          {new Date(
                                            version.created_at,
                                          ).toLocaleString()}
                                        </span>
                                      </div>

                                      {isCurrent ? (
                                        <span className="rounded-full border border-green-800 px-2.5 py-0.5 text-xs font-medium text-green-300">
                                          Current
                                        </span>
                                      ) : (
                                        <Button
                                          variant="secondary"
                                          disabled={
                                            template.status === "archived" ||
                                            publishingTemplateVersion !== null
                                          }
                                          onClick={() =>
                                            handlePublishTemplateVersion(
                                              template.id,
                                              version.version,
                                            )
                                          }
                                        >
                                          {publishingTemplateVersion ===
                                          version.version
                                            ? "Publishing..."
                                            : "Publish"}
                                        </Button>
                                      )}
                                    </li>
                                  );
                                })}
                              </ul>
                            )}

                          {templateVersions !== null &&
                            templateVersions.length > 1 && (
                              <div className="mt-4 border-t border-slate-800 pt-4">
                                <h4 className="text-sm font-semibold text-slate-300">
                                  Compare versions
                                </h4>

                                <form
                                  onSubmit={(event) =>
                                    handleDiffTemplateVersions(event, template.id)
                                  }
                                  className="mt-3 flex flex-wrap items-end gap-3"
                                  noValidate
                                >
                                  <div>
                                    <label
                                      htmlFor={`template_diff_from_${template.id}`}
                                      className="block text-sm font-medium text-slate-200"
                                    >
                                      From
                                    </label>

                                    <select
                                      id={`template_diff_from_${template.id}`}
                                      className="mt-2 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                                      value={templateDiffFrom}
                                      onChange={(event) =>
                                        setTemplateDiffFrom(event.target.value)
                                      }
                                    >
                                      <option value="" disabled>
                                        Select a version
                                      </option>
                                      {templateVersions.map((version) => (
                                        <option
                                          key={version.id}
                                          value={version.version}
                                        >
                                          Version {version.version}
                                        </option>
                                      ))}
                                    </select>
                                  </div>

                                  <div>
                                    <label
                                      htmlFor={`template_diff_to_${template.id}`}
                                      className="block text-sm font-medium text-slate-200"
                                    >
                                      To
                                    </label>

                                    <select
                                      id={`template_diff_to_${template.id}`}
                                      className="mt-2 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                                      value={templateDiffTo}
                                      onChange={(event) =>
                                        setTemplateDiffTo(event.target.value)
                                      }
                                    >
                                      <option value="" disabled>
                                        Select a version
                                      </option>
                                      {templateVersions.map((version) => (
                                        <option
                                          key={version.id}
                                          value={version.version}
                                        >
                                          Version {version.version}
                                        </option>
                                      ))}
                                    </select>
                                  </div>

                                  <Button
                                    type="submit"
                                    disabled={
                                      templateDiffLoading ||
                                      !templateDiffFrom ||
                                      !templateDiffTo
                                    }
                                  >
                                    {templateDiffLoading
                                      ? "Comparing..."
                                      : "Show diff"}
                                  </Button>
                                </form>

                                {templateDiffError && (
                                  <div className="mt-4">
                                    <ErrorText message={templateDiffError} />
                                  </div>
                                )}

                                {templateDiffResult &&
                                  Object.keys(templateDiffResult.changes).length ===
                                    0 && (
                                    <p className="mt-4 text-sm text-slate-400">
                                      No differences between version{" "}
                                      {templateDiffResult.from_version} and version{" "}
                                      {templateDiffResult.to_version}.
                                    </p>
                                  )}

                                {templateDiffResult &&
                                  Object.keys(templateDiffResult.changes).length >
                                    0 && (
                                    <div className="mt-4 overflow-x-auto">
                                      <table className="w-full text-left text-sm">
                                        <thead>
                                          <tr className="text-slate-400">
                                            <th className="pb-2 pr-4 font-medium">
                                              Field
                                            </th>
                                            <th className="pb-2 pr-4 font-medium">
                                              Version {templateDiffResult.from_version}
                                            </th>
                                            <th className="pb-2 font-medium">
                                              Version {templateDiffResult.to_version}
                                            </th>
                                          </tr>
                                        </thead>
                                        <tbody>
                                          {Object.entries(
                                            templateDiffResult.changes,
                                          ).map(([field, change]) => (
                                            <tr
                                              key={field}
                                              className="border-t border-slate-800"
                                            >
                                              <td className="py-2 pr-4 font-medium">
                                                {field}
                                              </td>
                                              <td className="py-2 pr-4 text-slate-400">
                                                {formatDiffValue(change.previous)}
                                              </td>
                                              <td className="py-2 text-slate-200">
                                                {formatDiffValue(change.current)}
                                              </td>
                                            </tr>
                                          ))}
                                        </tbody>
                                      </table>
                                    </div>
                                  )}
                              </div>
                            )}
                        </div>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
        </>
      )}

      {activeTab === "technical" && (
        <div className="mt-6 space-y-8">
          <Card>
            <h2 className="text-lg font-semibold">Speech behavior</h2>
            <p className="mt-1 text-sm text-slate-400">
              Saving posts a full new version snapshot - versions are immutable.
            </p>

            {versionError && (
              <div className="mt-4">
                <ErrorText message={versionError} />
              </div>
            )}
            {versionSaved && (
              <p className="mt-4 text-sm text-green-400">
                Saved as version {versionSaved.version}.
              </p>
            )}

            <form onSubmit={handleSaveVersion} className="mt-4 space-y-4" noValidate>
              <div>
                <label
                  htmlFor="speech_rate"
                  className="block text-sm font-medium text-slate-200"
                >
                  Speech rate
                </label>

                <input
                  id="speech_rate"
                  type="number"
                  min={0.5}
                  max={2}
                  step={0.1}
                  disabled={savingVersion}
                  className="mt-2 w-32 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                  value={speechRate}
                  onChange={(event) =>
                    setSpeechRate(event.target.valueAsNumber)
                  }
                />
              </div>

              <div>
                <label
                  htmlFor="turn_sensitivity"
                  className="block text-sm font-medium text-slate-200"
                >
                  Turn sensitivity
                </label>

                <input
                  id="turn_sensitivity"
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  disabled={savingVersion}
                  className="mt-2 w-32 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                  value={turnSensitivity}
                  onChange={(event) =>
                    setTurnSensitivity(event.target.valueAsNumber)
                  }
                />
              </div>

              <div>
                <label
                  htmlFor="creativity"
                  className="block text-sm font-medium text-slate-200"
                >
                  Creativity
                </label>

                <input
                  id="creativity"
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  disabled={savingVersion}
                  className="mt-2 w-32 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                  value={creativity}
                  onChange={(event) =>
                    setCreativity(event.target.valueAsNumber)
                  }
                />
              </div>

              <div>
                <label
                  htmlFor="ambient_sound"
                  className="block text-sm font-medium text-slate-200"
                >
                  Ambient sound
                </label>

                <select
                  id="ambient_sound"
                  disabled={savingVersion}
                  className="mt-2 w-full max-w-md rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                  value={ambientSound}
                  onChange={(event) => setAmbientSound(event.target.value)}
                >
                  <option value="">None</option>
                  <option value="office">Office</option>
                  <option value="conference_room">Conference room</option>
                </select>
              </div>

              <div>
                <label
                  htmlFor="ambient_sound_volume"
                  className="block text-sm font-medium text-slate-200"
                >
                  Ambient sound volume
                </label>

                <input
                  id="ambient_sound_volume"
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  disabled={savingVersion || !ambientSound}
                  className="mt-2 w-32 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600 disabled:opacity-50"
                  placeholder="0.0-1.0"
                  value={ambientSoundVolume}
                  onChange={(event) => setAmbientSoundVolume(event.target.value)}
                />
              </div>

              <div className="rounded-lg border border-slate-800 px-4 py-3">
                <p className="text-sm font-medium text-slate-300">Call settings</p>
                <p className="mt-1 text-xs text-slate-500">
                  Not yet enforced on a real call - configuration only until telephony is built.
                </p>

                <div className="mt-4 space-y-4">
                  <div>
                    <label
                      htmlFor="max_call_duration_seconds"
                      className="block text-sm font-medium text-slate-200"
                    >
                      Max call duration (seconds)
                    </label>

                    <input
                      id="max_call_duration_seconds"
                      type="number"
                      min={30}
                      max={3600}
                      disabled={savingVersion}
                      className="mt-2 w-32 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                      placeholder="Optional"
                      value={maxCallDurationSeconds}
                      onChange={(event) => setMaxCallDurationSeconds(event.target.value)}
                    />
                  </div>

                  <div>
                    <label
                      htmlFor="max_silence_timeout_seconds"
                      className="block text-sm font-medium text-slate-200"
                    >
                      Max silence timeout (seconds)
                    </label>

                    <input
                      id="max_silence_timeout_seconds"
                      type="number"
                      min={5}
                      max={300}
                      disabled={savingVersion}
                      className="mt-2 w-32 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                      placeholder="Optional"
                      value={maxSilenceTimeoutSeconds}
                      onChange={(event) => setMaxSilenceTimeoutSeconds(event.target.value)}
                    />
                  </div>

                  <div className="flex items-center gap-2">
                    <input
                      id="record_calls"
                      type="checkbox"
                      disabled={savingVersion}
                      className="h-4 w-4 rounded border-slate-700 bg-slate-950 focus:outline-none focus:ring-2 focus:ring-slate-600"
                      checked={recordCalls}
                      onChange={(event) => setRecordCalls(event.target.checked)}
                    />
                    <label htmlFor="record_calls" className="text-sm text-slate-200">
                      Record calls
                    </label>
                  </div>

                  <div className="flex items-center gap-2">
                    <input
                      id="auto_delete_on_declined_consent"
                      type="checkbox"
                      disabled={savingVersion}
                      className="h-4 w-4 rounded border-slate-700 bg-slate-950 focus:outline-none focus:ring-2 focus:ring-slate-600"
                      checked={autoDeleteOnDeclinedConsent}
                      onChange={(event) =>
                        setAutoDeleteOnDeclinedConsent(event.target.checked)
                      }
                    />
                    <label
                      htmlFor="auto_delete_on_declined_consent"
                      className="text-sm text-slate-200"
                    >
                      Automatically delete the recording if the caller declines consent
                    </label>
                  </div>
                </div>
              </div>

              <Button
                type="submit"
                disabled={
                  savingVersion ||
                  !voiceId ||
                  !greeting.trim() ||
                  (!!selectedPromptTemplateId && !selectedPromptVersion)
                }
              >
                {savingVersion ? "Saving..." : "Save as new version"}
              </Button>
            </form>
          </Card>

          <Card>
            <h2 className="text-lg font-semibold">Technical Terms</h2>
            <p className="mt-1 text-sm text-slate-400">
              Terms, meanings, and phonetic pronunciation overrides for this assistant.
            </p>

            {deleteError && (
              <div className="mt-4">
                <ErrorText message={deleteError} />
              </div>
            )}

            {glossaryError && (
              <div className="mt-4">
                <ErrorText message={glossaryError} />
              </div>
            )}

            {glossaryEntries === null && !glossaryError && (
              <div className="mt-4">
                <LoadingState message="Loading technical terms..." />
              </div>
            )}

            {glossaryEntries !== null && glossaryEntries.length === 0 && (
              <div className="mt-4">
                <EmptyState message="No technical terms yet. Add one below to get started." />
              </div>
            )}

            {glossaryEntries !== null && glossaryEntries.length > 0 && (
              <ul className="mt-4 space-y-3">
                {glossaryEntries.map((entry) =>
                  editingId === entry.id ? (
                    <li
                      key={entry.id}
                      className="rounded-xl border border-slate-800 px-4 py-3"
                    >
                      {editError && (
                        <div className="mb-3">
                          <ErrorText message={editError} />
                        </div>
                      )}

                      <div className="flex flex-wrap gap-3">
                        <input
                          aria-label="Edit term"
                          className="min-w-40 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                          required
                          disabled={savingEdit}
                          value={editTerm}
                          onChange={(event) => setEditTerm(event.target.value)}
                        />

                        <input
                          aria-label="Edit meaning"
                          className="min-w-48 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                          placeholder="Meaning (optional)"
                          disabled={savingEdit}
                          value={editMeaning}
                          onChange={(event) => setEditMeaning(event.target.value)}
                        />

                        <input
                          aria-label="Edit phonetic spelling"
                          className="min-w-40 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                          placeholder="Phonetic spelling (optional)"
                          disabled={savingEdit}
                          value={editPhoneticSpelling}
                          onChange={(event) =>
                            setEditPhoneticSpelling(event.target.value)
                          }
                        />

                        <input
                          aria-label="Edit boost weight"
                          type="number"
                          min={0}
                          max={1}
                          step={0.05}
                          className="w-28 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                          disabled={savingEdit}
                          value={editBoostWeight}
                          onChange={(event) =>
                            setEditBoostWeight(event.target.valueAsNumber)
                          }
                        />
                      </div>

                      <div className="mt-3 flex gap-3">
                        <Button
                          disabled={savingEdit || !editTerm.trim()}
                          onClick={() => handleSaveEdit(entry.id)}
                        >
                          {savingEdit ? "Saving..." : "Save"}
                        </Button>
                        <Button
                          variant="secondary"
                          disabled={savingEdit}
                          onClick={handleCancelEdit}
                        >
                          Cancel
                        </Button>
                      </div>
                    </li>
                  ) : (
                    <li
                      key={entry.id}
                      className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 px-4 py-3"
                    >
                      <div>
                        <span className="font-medium">{entry.term}</span>
                        {entry.meaning && (
                          <span className="ml-3 text-sm text-slate-400">
                            {entry.meaning}
                          </span>
                        )}
                        {entry.phonetic_spelling && (
                          <span className="ml-3 text-sm text-slate-500">
                            /{entry.phonetic_spelling}/
                          </span>
                        )}
                        <span className="ml-3 text-sm text-slate-500">
                          boost {entry.stt_boost_weight}
                        </span>
                      </div>

                      <div className="flex gap-2">
                        <Button
                          variant="secondary"
                          disabled={deletingId === entry.id}
                          onClick={() => handleStartEdit(entry)}
                        >
                          Edit
                        </Button>
                        <Button
                          variant="secondary"
                          disabled={deletingId === entry.id}
                          onClick={() => handleDeleteEntry(entry.id)}
                        >
                          {deletingId === entry.id ? "Deleting..." : "Delete"}
                        </Button>
                      </div>
                    </li>
                  ),
                )}
              </ul>
            )}

            <form
              onSubmit={handleCreateEntry}
              className="mt-6 border-t border-slate-800 pt-4"
              noValidate
            >
              <h3 className="text-sm font-semibold text-slate-300">Add entry</h3>

              {createEntryError && (
                <div className="mt-3">
                  <ErrorText message={createEntryError} />
                </div>
              )}

              <div className="mt-3 flex flex-wrap gap-3">
                <input
                  aria-label="Term"
                  className="min-w-40 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                  placeholder="Term"
                  required
                  disabled={creatingEntry}
                  value={newTerm}
                  onChange={(event) => setNewTerm(event.target.value)}
                />

                <input
                  aria-label="Meaning"
                  className="min-w-48 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                  placeholder="Meaning (optional)"
                  disabled={creatingEntry}
                  value={newMeaning}
                  onChange={(event) => setNewMeaning(event.target.value)}
                />

                <input
                  aria-label="Phonetic spelling"
                  className="min-w-40 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white placeholder:text-slate-500 focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
                  placeholder="Phonetic spelling (optional)"
                  disabled={creatingEntry}
                  value={newPhoneticSpelling}
                  onChange={(event) => setNewPhoneticSpelling(event.target.value)}
                />

                <input
                  aria-label="Boost weight"
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  className="w-28 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                  disabled={creatingEntry}
                  value={newBoostWeight}
                  onChange={(event) => setNewBoostWeight(event.target.valueAsNumber)}
                />

                <Button type="submit" disabled={creatingEntry || !newTerm.trim()}>
                  {creatingEntry ? "Adding..." : "Add"}
                </Button>
              </div>
            </form>
          </Card>
        </div>
      )}

      <div className="mt-8">
        <Card>
          <h2 className="text-lg font-semibold">Version history</h2>

          {publishError && (
            <div className="mt-4">
              <ErrorText message={publishError} />
            </div>
          )}

          {versionsError && (
            <div className="mt-4">
              <ErrorText message={versionsError} />
            </div>
          )}

          {versions === null && !versionsError && (
            <div className="mt-4">
              <LoadingState message="Loading versions..." />
            </div>
          )}

          {versions !== null && versions.length === 0 && (
            <div className="mt-4">
              <EmptyState message="No versions saved yet. Save one above to get started." />
            </div>
          )}

          {versions !== null && versions.length > 0 && (
            <ul className="mt-4 space-y-3">
              {versions.map((version) => {
                const isCurrent = version.id === assistant.current_version_id;

                return (
                  <li
                    key={version.id}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 px-4 py-3"
                  >
                    <div>
                      <span className="font-medium">Version {version.version}</span>
                      <span className="ml-3 text-sm text-slate-500">
                        {new Date(version.created_at).toLocaleString()}
                      </span>
                    </div>

                    {isCurrent ? (
                      <span className="rounded-full border border-green-800 px-2.5 py-0.5 text-xs font-medium text-green-300">
                        Current
                      </span>
                    ) : (
                      <Button
                        variant="secondary"
                        disabled={archived || publishingVersion !== null}
                        onClick={() => handlePublish(version.version)}
                      >
                        {publishingVersion === version.version
                          ? "Publishing..."
                          : "Publish"}
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          {versions !== null && versions.length > 1 && (
            <div className="mt-6 border-t border-slate-800 pt-4">
              <h3 className="text-sm font-semibold text-slate-300">
                Compare versions
              </h3>

              <form
                onSubmit={handleDiff}
                className="mt-3 flex flex-wrap items-end gap-3"
                noValidate
              >
                <div>
                  <label
                    htmlFor="diff_from"
                    className="block text-sm font-medium text-slate-200"
                  >
                    From
                  </label>

                  <select
                    id="diff_from"
                    className="mt-2 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                    value={diffFrom}
                    onChange={(event) => setDiffFrom(event.target.value)}
                  >
                    <option value="" disabled>
                      Select a version
                    </option>
                    {versions.map((version) => (
                      <option key={version.id} value={version.version}>
                        Version {version.version}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label
                    htmlFor="diff_to"
                    className="block text-sm font-medium text-slate-200"
                  >
                    To
                  </label>

                  <select
                    id="diff_to"
                    className="mt-2 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-slate-600"
                    value={diffTo}
                    onChange={(event) => setDiffTo(event.target.value)}
                  >
                    <option value="" disabled>
                      Select a version
                    </option>
                    {versions.map((version) => (
                      <option key={version.id} value={version.version}>
                        Version {version.version}
                      </option>
                    ))}
                  </select>
                </div>

                <Button type="submit" disabled={diffLoading || !diffFrom || !diffTo}>
                  {diffLoading ? "Comparing..." : "Show diff"}
                </Button>
              </form>

              {diffError && (
                <div className="mt-4">
                  <ErrorText message={diffError} />
                </div>
              )}

              {diffResult && Object.keys(diffResult.changes).length === 0 && (
                <p className="mt-4 text-sm text-slate-400">
                  No differences between version {diffResult.from_version} and
                  version {diffResult.to_version}.
                </p>
              )}

              {diffResult && Object.keys(diffResult.changes).length > 0 && (
                <div className="mt-4 overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="text-slate-400">
                        <th className="pb-2 pr-4 font-medium">Field</th>
                        <th className="pb-2 pr-4 font-medium">
                          Version {diffResult.from_version}
                        </th>
                        <th className="pb-2 font-medium">
                          Version {diffResult.to_version}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(diffResult.changes).map(
                        ([field, change]) => (
                          <tr key={field} className="border-t border-slate-800">
                            <td className="py-2 pr-4 font-medium">{field}</td>
                            <td className="py-2 pr-4 text-slate-400">
                              {formatDiffValue(change.previous)}
                            </td>
                            <td className="py-2 text-slate-200">
                              {formatDiffValue(change.current)}
                            </td>
                          </tr>
                        ),
                      )}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </Card>
      </div>

    </PageShell>
  );
}
