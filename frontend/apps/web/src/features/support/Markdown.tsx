import { Fragment, type ReactNode } from "react";

/**
 * Renders the Markdown subset of help articles as React elements (never as HTML, so article
 * text cannot inject markup): "## " headings, "- " lists, paragraphs and **bold**.
 */
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) =>
    part.startsWith("**") && part.endsWith("**") && part.length > 4 ? (
      <strong key={index} className="font-semibold text-fg">
        {part.slice(2, -2)}
      </strong>
    ) : (
      <Fragment key={index}>{part}</Fragment>
    ),
  );
}

export function Markdown({ source }: { source: string }) {
  const blocks = source.trim().split(/\n\s*\n/);
  return (
    <div className="flex flex-col gap-4 text-[0.95rem] leading-relaxed text-fg">
      {blocks.map((block, index) => {
        const lines = block.split("\n");
        if (lines[0]?.startsWith("## ")) {
          const rest = lines.slice(1).join(" ");
          return (
            <Fragment key={index}>
              <h2 className="mt-2 text-lg font-semibold text-fg">{lines[0].slice(3)}</h2>
              {rest && <p>{inline(rest)}</p>}
            </Fragment>
          );
        }
        if (lines.every((line) => line.startsWith("- "))) {
          return (
            <ul key={index} className="flex list-disc flex-col gap-1.5 pl-6">
              {lines.map((line, i) => (
                <li key={i}>{inline(line.slice(2))}</li>
              ))}
            </ul>
          );
        }
        return <p key={index}>{inline(lines.join(" "))}</p>;
      })}
    </div>
  );
}
