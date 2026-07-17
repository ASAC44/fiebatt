const TOOL_MARKUP_START = /<\s*(?:(?:[|｜]\s*)?DSML(?:\s*[|｜])?\s*)?(?:function[\s_]?calls?|tool[\s_]?calls?|invoke\b|parameter\b)/i;

export function hasAgentToolMarkup(text: string): boolean {
  return TOOL_MARKUP_START.test(text);
}

/** Keep model prose while removing raw tool-protocol markup from the UI. */
export function cleanAgentText(text: string): string {
  const marker = TOOL_MARKUP_START.exec(text);
  return marker ? text.slice(0, marker.index).trimEnd() : text;
}
