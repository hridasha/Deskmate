/** Strip Markdown markers for places that can only show plain text (3D speech bubbles, previews, logs). */
export function plainText(s: string): string {
  return s
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/__(.+?)__/g, "$1")
    .replace(/(^|[\s(])\*(\S[^*\n]*?)\*(?=[\s).,!?:;]|$)/g, "$1$2")
    .replace(/`([^`\n]+)`/g, "$1")
    .replace(/^\s*[-*]\s+/gm, "• ")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\s*\n\s*/g, " ")
    .trim();
}
