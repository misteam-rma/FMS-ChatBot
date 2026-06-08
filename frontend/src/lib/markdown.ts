import { marked } from "marked";
import DOMPurify from "dompurify";

// Open all links in a new tab with safe rel attributes. DOMPurify strips
// target by default, so we add it back via an afterSanitize hook (and ensure
// rel includes noopener noreferrer to prevent reverse-tabnabbing).
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

export function renderMarkdown(content: string): string {
  try {
    const rawHtml = marked.parse(content, {
      async: false,
      breaks: true,   // single newlines -> <br>, matches chat expectations
      gfm: true,      // autolink bare URLs, tables, etc.
    }) as string;

    return DOMPurify.sanitize(rawHtml, {
      ADD_ATTR: ["target", "rel"],
    });
  } catch (error) {
    console.error("Markdown rendering error:", error);
    return content; // Fallback
  }
}
