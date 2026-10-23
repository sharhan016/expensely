export type Inline = { text: string; strong: boolean };
export type Block = { kind: "p"; parts: Inline[] } | { kind: "ul"; items: Inline[][] };

// Assistant replies are plain text with two conventions: a line starting "- " is a list item and
// **text** is emphasis. Everything else is a paragraph. Nothing is ever interpreted as HTML.
export function inlineParts(line: string): Inline[] {
  return line
    .split(/(\*\*[^*]+\*\*)/g)
    .filter(Boolean)
    .map(part => (part.length > 4 && part.startsWith("**") && part.endsWith("**") ? { text: part.slice(2, -2), strong: true } : { text: part, strong: false }));
}

export function parseAssistantText(content: string): Block[] {
  const blocks: Block[] = [];
  for (const line of content.split("\n")) {
    const last = blocks[blocks.length - 1];
    if (line.startsWith("- ")) {
      if (last?.kind === "ul") last.items.push(inlineParts(line.slice(2)));
      else blocks.push({ kind: "ul", items: [inlineParts(line.slice(2))] });
    } else if (line.trim()) {
      blocks.push({ kind: "p", parts: inlineParts(line) });
    }
  }
  return blocks;
}
