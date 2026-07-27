import type { ApiCitation } from "@/lib/api/types";
import type { AssistantMessage, Citation, MessageSegment } from "@/lib/types/chat";

// Mirrors apps/api/services/generator.py's CITATION_BRACKET/CITATION_NUMBER:
// a bracket can legitimately contain more than one marker (Gemini has been
// observed grouping citations, e.g. "[2, 3]" — FEAT-010's self-audit,
// apps/api/services/generator.py's own comment), so this extracts every
// digit run inside each bracket rather than assuming one marker per
// bracket. Content reaching the client has already had unsupported
// markers stripped server-side (_strip_dropped_markers), but a
// hallucinated marker (one the model invented outside 1..num_chunks) is
// NOT stripped — only dropped-but-otherwise-valid positions are — so a
// bracket number with no matching citation is a real, if rare, possible
// shape here, not just defensive paranoia.
const CITATION_BRACKET = /\[([^[\]]*)]/g;
const CITATION_NUMBER = /\d+/g;

// FEAT-020 (2026-07-27): page_number's real meaning depends on the
// source document's format — confirmed live per format, not assumed
// (.agent/SCHEMA.md's page_number note, apps/api/routes/ingest.py's
// SUPPORTED_MIME_TYPES). PPTX's page_number really is the 1-indexed
// slide index. DOCX/HTML give Docling no page/location concept at all,
// so page_number is always a fixed `1` sentinel there — real for PDF,
// meaningless for these two. Any mime_type not recognized here
// (including PDF) falls through to the historical "page" behavior,
// matching what every citation looked like before this format
// distinction existed.
const SLIDE_MIME_TYPES = new Set([
  "application/vnd.openxmlformats-officedocument.presentationml.presentation", // .pptx
]);
const NO_REAL_LOCATION_MIME_TYPES = new Set([
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document", // .docx
  "text/html",
]);

function citationLocation(mimeType: string, pageNumber: number): Citation["location"] {
  if (NO_REAL_LOCATION_MIME_TYPES.has(mimeType)) return null;
  if (SLIDE_MIME_TYPES.has(mimeType)) return { kind: "slide", number: pageNumber };
  return { kind: "page", number: pageNumber };
}

function apiCitationToClientCitation(id: string, c: ApiCitation): Citation {
  return {
    id,
    n: c.marker,
    documentName: c.document_name,
    location: citationLocation(c.document_mime_type, c.page_number),
    // Backend only ever sends 'supported' | 'partial' citations to the
    // client (POST /query drops 'unsupported' from its response array,
    // GET /conversations/{id}/messages' list_citations_for_messages()
    // filters the same way) — narrowed here rather than widening
    // Citation.verdict to include 'unsupported', since nothing renders
    // that case and a stray 'unsupported' row reaching this point would
    // mean a real contract violation worth a loud runtime error, not a
    // silently-accepted type.
    verdict: c.verdict as "supported" | "partial",
    // supporting_quote is null whenever the verifier couldn't ground a
    // verbatim quote (always true for figure citations — figure chunks
    // have no text content to quote from) — snippet (the raw chunk
    // content) is always present, so it's the honest fallback rather
    // than showing an empty excerpt.
    excerpt: c.supporting_quote ?? c.snippet,
    isFigure: c.element_type === "figure",
    figureUrl: c.figure_url,
  };
}

/** Shared by lib/api/query.ts (a live answer) and lib/api/conversations.ts
 * (a historical one) — same citation shape, same bracket-parsing rules,
 * one place so the two can never silently disagree on how `[N]` markers
 * in `content` map to entries in `citations`. */
export function buildAssistantMessage(
  id: string,
  content: string,
  citations: ApiCitation[]
): AssistantMessage {
  const clientCitations = citations.map((c) => apiCitationToClientCitation(`${id}-c${c.marker}`, c));
  const clientByMarker = new Map(clientCitations.map((c) => [c.n, c]));

  const segments: MessageSegment[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  CITATION_BRACKET.lastIndex = 0;

  while ((match = CITATION_BRACKET.exec(content)) !== null) {
    if (match.index > cursor) {
      segments.push({ type: "text", text: content.slice(cursor, match.index) });
    }
    const numbers = match[1].match(CITATION_NUMBER) ?? [];
    let anyResolved = false;
    for (const raw of numbers) {
      const marker = Number(raw);
      const citation = clientByMarker.get(marker);
      if (citation) {
        segments.push({ type: "citation", citationId: citation.id });
        anyResolved = true;
      }
    }
    // A bracket with no marker resolving to a real citation (a
    // hallucinated marker that survived server-side stripping, or any
    // other unrecognized bracket-shaped text) — render it as inert
    // plain text exactly as written rather than silently dropping it or
    // crashing on a missing citation lookup.
    if (!anyResolved) {
      segments.push({ type: "text", text: match[0] });
    }
    cursor = match.index + match[0].length;
  }
  if (cursor < content.length) {
    segments.push({ type: "text", text: content.slice(cursor) });
  }

  return {
    id,
    role: "assistant",
    segments,
    citations: clientCitations,
  };
}
