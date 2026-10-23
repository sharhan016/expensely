import { Fragment } from "react";
import { Inline, parseAssistantText } from "@/lib/assistantText";

function Parts({ parts }: { parts: Inline[] }) {
  return <>{parts.map((part, index) => (part.strong ? <strong key={index}>{part.text}</strong> : <Fragment key={index}>{part.text}</Fragment>))}</>;
}

export function AssistantText({ content }: { content: string }) {
  return (
    <div className="assistant-text">
      {parseAssistantText(content).map((block, index) =>
        block.kind === "p" ? <p key={index}><Parts parts={block.parts} /></p> : <ul key={index}>{block.items.map((item, i) => <li key={i}><Parts parts={item} /></li>)}</ul>,
      )}
    </div>
  );
}
