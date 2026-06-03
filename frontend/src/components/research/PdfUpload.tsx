"use client";

import { useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  FileText,
  Loader2,
  UploadCloud,
} from "lucide-react";

import type {
  PaperChunkPipelineResponse,
  PaperEmbeddingPipelineResponse,
  PaperMetadata,
  PaperUploadResponse,
  ResearchManifest,
} from "@/types/api";
import type { ChangeEvent, DragEvent } from "react";

const RESEARCH_API_BASE = "http://localhost:8000/api/v1/research";

type PipelineStepId = "upload" | "chunk" | "embed" | "manifest";
type PipelineStepStatus = "pending" | "running" | "complete" | "error";

interface PipelineStep {
  id: PipelineStepId;
  label: string;
  status: PipelineStepStatus;
}

interface PdfUploadProps {
  onPipelineComplete?: (paper: PaperMetadata, manifest: ResearchManifest) => void;
  className?: string;
}

const initialSteps: PipelineStep[] = [
  { id: "upload", label: "Upload", status: "pending" },
  { id: "chunk", label: "Chunk", status: "pending" },
  { id: "embed", label: "Embed", status: "pending" },
  { id: "manifest", label: "Manifest", status: "pending" },
];

function isPdf(file: File) {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

function getApiError(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") {
      return detail;
    }
  }

  return fallback;
}

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  const payload = (await response.json().catch(() => null)) as unknown;

  if (!response.ok) {
    throw new Error(getApiError(payload, fallback));
  }

  return payload as T;
}

function statusIcon(status: PipelineStepStatus) {
  if (status === "complete") {
    return <CheckCircle2 className="h-4 w-4 text-emerald-200" aria-hidden="true" />;
  }

  if (status === "running") {
    return <Loader2 className="h-4 w-4 animate-spin text-cyan-200" aria-hidden="true" />;
  }

  if (status === "error") {
    return <AlertTriangle className="h-4 w-4 text-red-300" aria-hidden="true" />;
  }

  return <Circle className="h-4 w-4 text-zinc-600" aria-hidden="true" />;
}

export function PdfUpload({ onPipelineComplete, className = "" }: PdfUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const pipelineLockRef = useRef(false);
  const [steps, setSteps] = useState<PipelineStep[]>(initialSteps);
  const [isDragging, setIsDragging] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [selectedFileName, setSelectedFileName] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState("Ready for PDF upload.");
  const [error, setError] = useState<string | null>(null);

  const progress = useMemo(() => {
    const completed = steps.filter((step) => step.status === "complete").length;
    return Math.round((completed / steps.length) * 100);
  }, [steps]);

  function setStepStatus(id: PipelineStepId, status: PipelineStepStatus) {
    setSteps((current) =>
      current.map((step) => (step.id === id ? { ...step, status } : step)),
    );
  }

  function resetPipeline() {
    setSteps(initialSteps);
    setError(null);
  }

  async function runPipeline(file: File) {
    if (pipelineLockRef.current) {
      return;
    }

    pipelineLockRef.current = true;

    if (!isPdf(file)) {
      setError("Only PDF files are supported.");
      setStatusMessage("The selected file was not a PDF.");
      pipelineLockRef.current = false;
      return;
    }

    resetPipeline();
    setIsRunning(true);
    setSelectedFileName(file.name);
    setStatusMessage(`Selected ${file.name}. Starting upload...`);

    try {
      const formData = new FormData();
      formData.append("file", file);

      setStepStatus("upload", "running");
      setStatusMessage("Uploading PDF to FastAPI...");
      const uploadResponse = await fetch(`${RESEARCH_API_BASE}/upload`, {
        method: "POST",
        body: formData,
      });
      const upload = await readJson<PaperUploadResponse>(uploadResponse, "PDF upload failed.");
      setStepStatus("upload", "complete");

      setStepStatus("chunk", "running");
      setStatusMessage("Chunking parsed markdown...");
      const chunkResponse = await fetch(`${RESEARCH_API_BASE}/${upload.paper_id}/chunk`, {
        method: "POST",
      });
      await readJson<PaperChunkPipelineResponse>(chunkResponse, "Chunking failed.");
      setStepStatus("chunk", "complete");

      setStepStatus("embed", "running");
      setStatusMessage("Embedding chunks into Qdrant...");
      const embedResponse = await fetch(`${RESEARCH_API_BASE}/${upload.paper_id}/embed`, {
        method: "POST",
      });
      await readJson<PaperEmbeddingPipelineResponse>(embedResponse, "Embedding failed.");
      setStepStatus("embed", "complete");

      setStepStatus("manifest", "running");
      setStatusMessage("Generating research manifest...");
      const manifestResponse = await fetch(`${RESEARCH_API_BASE}/${upload.paper_id}/manifest`, {
        method: "POST",
      });
      const manifest = await readJson<ResearchManifest>(
        manifestResponse,
        "Manifest generation failed.",
      );
      setStepStatus("manifest", "complete");
      setStatusMessage("Research pipeline complete.");

      onPipelineComplete?.({ ...upload.metadata, id: upload.paper_id }, manifest);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Research pipeline failed.";
      setError(message);
      setStatusMessage("Pipeline stopped.");
      setSteps((current) =>
        current.map((step) =>
          step.status === "running" ? { ...step, status: "error" } : step,
        ),
      );
    } finally {
      setIsRunning(false);
      if (inputRef.current) {
        inputRef.current.value = "";
      }
      pipelineLockRef.current = false;
    }
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (!isRunning) {
      setIsDragging(true);
    }
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragging(false);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragging(false);

    const file = event.dataTransfer.files.item(0);
    if (file && !isRunning) {
      void runPipeline(file);
    }
  }

  function handleFileInput(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.item(0);
    if (file && !isRunning) {
      void runPipeline(file);
    }
  }

  return (
    <section
      className={`relative overflow-hidden rounded-lg border border-white/10 bg-zinc-950 p-4 shadow-2xl shadow-black/30 ${className}`}
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-emerald-200/50 to-transparent" />

      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`grid min-h-[184px] place-items-center rounded-md border border-dashed p-5 text-center transition ${
          isDragging
            ? "border-cyan-200/70 bg-cyan-300/10"
            : "border-white/10 bg-black/30"
        } ${isRunning ? "opacity-80" : ""}`}
      >
        <div className="mx-auto max-w-md">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border border-white/10 bg-white/[0.05] text-cyan-200">
            <UploadCloud className="h-6 w-6" aria-hidden="true" />
          </div>
          <h2 className="mt-4 text-lg font-semibold text-white">Upload research PDF</h2>
          <p className="mt-2 text-sm leading-6 text-zinc-500">
            Drop a PDF to parse, chunk, embed, and generate its manifest.
          </p>
          <div className="mt-4">
            <input
              ref={inputRef}
              type="file"
              accept="application/pdf,.pdf"
              onClick={(event) => {
                event.currentTarget.value = "";
                setError(null);
                setSelectedFileName(null);
                setStatusMessage("Choose a PDF from the file picker.");
              }}
              onChange={handleFileInput}
              disabled={isRunning}
              className="sr-only"
              id="research-pdf-upload"
            />
            <label
              htmlFor="research-pdf-upload"
              className={`inline-flex h-10 cursor-pointer items-center justify-center gap-2 rounded-md border border-cyan-300/30 bg-cyan-300/10 px-4 text-sm font-semibold text-cyan-100 transition hover:bg-cyan-300/15 ${
                isRunning ? "pointer-events-none opacity-50" : ""
              }`}
            >
              <FileText className="h-4 w-4" aria-hidden="true" />
              {isRunning ? "Processing" : "Select PDF"}
            </label>
          </div>
        </div>
      </div>

      <div className="mt-4">
        <div className="flex items-center justify-between gap-3 text-xs text-zinc-500">
          <span className="truncate">{selectedFileName ?? statusMessage}</span>
          <span>{progress}%</span>
        </div>
        <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/[0.06]">
          <div
            className="h-full rounded-full bg-cyan-300 transition-all duration-300"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      <div className="mt-4 grid gap-2 sm:grid-cols-4">
        {steps.map((step) => (
          <div
            key={step.id}
            className="flex min-h-10 items-center gap-2 rounded-md border border-white/10 bg-white/[0.03] px-3 py-2 text-sm"
          >
            {statusIcon(step.status)}
            <span className="font-medium text-zinc-200">{step.label}</span>
          </div>
        ))}
      </div>

      {error ? (
        <p className="mt-4 rounded-md border border-red-300/20 bg-red-300/10 px-3 py-2 text-sm text-red-200">
          {error}
        </p>
      ) : null}
    </section>
  );
}
