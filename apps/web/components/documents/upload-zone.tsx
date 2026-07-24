"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { ApiError, uploadDocument } from "@/lib/api/documents";
import type { DocumentStatus } from "@/lib/status-styles";

export interface UploadedDocument {
  id: string;
  filename: string;
  status: DocumentStatus;
  created_at: string;
}

export interface UploadZoneProps {
  onUploadComplete: (doc: UploadedDocument) => void;
}

export function UploadZone({ onUploadComplete }: UploadZoneProps) {
  const [uploading, setUploading] = React.useState(false);
  const [name, setName] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);

  async function startUpload(file: File) {
    if (uploading) return;
    setError(null);
    setUploading(true);
    setName(file.name);

    try {
      const result = await uploadDocument(file);
      onUploadComplete({
        id: result.document_id,
        filename: file.name,
        status: result.status,
        created_at: result.created_at,
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed. Try again.");
    } finally {
      setUploading(false);
    }
  }

  function handleFiles(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    if (file.type !== "application/pdf") {
      setError("Only PDF files are supported.");
      return;
    }
    startUpload(file);
  }

  if (uploading) {
    return (
      <div className="rounded-xl border-[1.5px] border-dashed border-dashed-line bg-drop-bg px-8 py-9 text-center">
        <div className="mx-auto max-w-[380px]">
          <p className="m-0 truncate font-serif text-lg font-medium">{name}</p>
          {/* No real byte-level progress is available — the Storage SDK's
           * upload() is fetch-based with no upload-progress event, so this
           * is an honest indeterminate indicator, not a fabricated percent. */}
          <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-panel-active">
            <div className="h-full w-1/3 animate-indeterminate-bar rounded-full bg-accent" />
          </div>
          <p className="m-0 mt-2 font-mono text-[11px] tracking-[0.08em] text-faint">
            UPLOADING…
          </p>
        </div>
      </div>
    );
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => inputRef.current?.click()}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
      }}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        handleFiles(e.dataTransfer.files);
      }}
      className="cursor-pointer rounded-xl border-[1.5px] border-dashed border-dashed-line bg-drop-bg px-8 py-9 text-center transition-colors hover:border-accent hover:bg-drop-bg-hover"
    >
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf"
        className="hidden"
        onChange={(e) => handleFiles(e.target.files)}
      />
      <div className="font-serif text-4xl leading-none text-accent">¶</div>
      <h2 className="m-0 mb-1 mt-3 font-serif text-[22px] font-medium">
        Drop a PDF to add it to your library
      </h2>
      <p className="mx-auto m-0 max-w-[380px] text-sm leading-relaxed text-muted">
        or <span className="font-medium text-accent">browse your files</span>{" "}
        — up to 50 MB, text is extracted and indexed automatically.
      </p>
      {error ? (
        <p className="m-0 mt-3 text-[13px] text-destructive">{error}</p>
      ) : null}
      {error ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="mt-2"
          onClick={(e) => {
            e.stopPropagation();
            setError(null);
          }}
        >
          Dismiss
        </Button>
      ) : null}
    </div>
  );
}
