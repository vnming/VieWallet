import axe from "axe-core";

/**
 * Runs axe-core on rendered markup and returns serious/critical violations (Phase 7
 * acceptance: none). Colour contrast is skipped here because jsdom has no layout or CSS
 * cascade; the E2E axe run in a real browser covers it.
 */
export async function seriousViolations(root: Element = document.body): Promise<string[]> {
  const result = await axe.run(root, {
    rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
    resultTypes: ["violations"],
  });
  return result.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}
