import { visit } from "unist-util-visit";
import type { Root, Text, PhrasingContent } from "mdast";

// A Unicode Private Use Area delimiter — cannot appear in real Gemini
// output (no legitimate input types it, no legitimate content contains
// it), and CommonMark has no special handling for PUA characters, so
// it's completely inert to markdown parsing. buildAssistantMessage()
// (lib/chat/parse-message.ts) already does the ONE real citation-
// bracket parse this project trusts (the server/client-shared
// CITATION_BRACKET/CITATION_NUMBER discipline documented there) — this
// plugin never re-parses `[N]` brackets itself. It only finds
// placeholders that message-bubble.tsx already substituted in place of
// buildAssistantMessage()'s "citation" segments, so a single
// react-markdown parse sees the whole message (correct block structure:
// headings, lists, multi-paragraph text) while still splicing a real
// citation marker back in at the exact right spot, including inside a
// list item or heading — something rendering each segment through an
// independent markdown parse cannot do correctly. The delimiter (not a
// bare digit match) is what keeps this from ever matching a real
// number in the message text, e.g. "the year 2010".
const PLACEHOLDER_START = String.fromCharCode(0xe000);
const PLACEHOLDER_END = String.fromCharCode(0xe001);

export function citationPlaceholder(index: number): string {
  return `${PLACEHOLDER_START}${index}${PLACEHOLDER_END}`;
}

const PLACEHOLDER_PATTERN = new RegExp(`${PLACEHOLDER_START}(\\d+)${PLACEHOLDER_END}`, "g");

interface CitationMarkerNode {
  type: "citationMarker";
  data: {
    hName: "citation-marker";
    hProperties: { placeholderindex: number };
  };
}

/** Splits any text node containing one or more citationPlaceholder()
 * strings into [text, citationMarker, text, citationMarker, ...], so
 * remark-rehype (via each citationMarker node's `data.hName`/
 * `data.hProperties`) emits a real `<citation-marker>` hast element at
 * that exact position — react-markdown's `components` prop then maps
 * that tag name to the real CitationMarker React component. */
export function remarkCitationMarkers() {
  return (tree: Root) => {
    visit(tree, "text", (node: Text, index, parent) => {
      if (index === undefined || !parent) return;
      PLACEHOLDER_PATTERN.lastIndex = 0;
      if (!PLACEHOLDER_PATTERN.test(node.value)) return;

      const replacement: PhrasingContent[] = [];
      let cursor = 0;
      PLACEHOLDER_PATTERN.lastIndex = 0;
      let match: RegExpExecArray | null;

      while ((match = PLACEHOLDER_PATTERN.exec(node.value)) !== null) {
        if (match.index > cursor) {
          replacement.push({ type: "text", value: node.value.slice(cursor, match.index) });
        }
        const citationNode: CitationMarkerNode = {
          type: "citationMarker",
          data: {
            hName: "citation-marker",
            hProperties: { placeholderindex: Number(match[1]) },
          },
        };
        replacement.push(citationNode as unknown as PhrasingContent);
        cursor = match.index + match[0].length;
      }
      if (cursor < node.value.length) {
        replacement.push({ type: "text", value: node.value.slice(cursor) });
      }

      parent.children.splice(index, 1, ...replacement);
      return index + replacement.length;
    });
  };
}
