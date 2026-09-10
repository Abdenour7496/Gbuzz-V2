import * as React from "react";
import type { KnowledgeCard } from "./knowledgeCardModel";

export function KnowledgeCardView({ card, busy, canReview, notice, onAction, onSource }: {
  card: KnowledgeCard; busy: boolean; canReview: boolean; notice: string;
  onAction: (action: string, question?: string) => void;
  onSource: (id: string) => void;
}) {
  const [question, setQuestion] = React.useState("");
  const [asking, setAsking] = React.useState(false);
  const [revising, setRevising] = React.useState(false);
  const [replacement, setReplacement] = React.useState("");
  const actionClass = "rounded-lg border border-border bg-background px-3 py-2 text-sm font-medium hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 disabled:opacity-50";
  const preview = card.summary.split("\n").filter((line) => !/^(Inspect: !knowledge|Approve: !knowledge|Proposal saved:|Revision:)/.test(line)).join("\n").trim();
  return (
    <section aria-label="Knowledge card" className="my-2 max-w-2xl overflow-hidden rounded-xl border border-border bg-background shadow-sm" data-testid="knowledge-card">
      <header className="flex items-start justify-between gap-4 border-b border-border bg-muted/40 p-4">
        <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Buzz knowledge</p>
          <h3 className="text-base font-semibold">{card.title}</h3></div>
        <span className="rounded-full border border-border bg-background px-2 py-1 text-xs capitalize">{card.state ?? (card.kind === "answer" ? "Cited answer" : "Workspace")}</span>
      </header>
      <div className="space-y-4 p-4">
        {card.kind === "help" ? <details><summary className="cursor-pointer text-sm font-medium">Knowledge workspace and commands</summary><p className="mt-3 whitespace-pre-wrap text-sm">{preview}</p></details> :
          <details open={card.kind !== "document"}>
            <summary className="cursor-pointer text-sm font-medium">{card.kind === "answer" ? "Answer and references" : "Findings and supporting evidence"}</summary>
            <p className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words text-sm leading-relaxed">{preview}</p>
          </details>}
        {card.sources.length > 0 && <div className="flex flex-wrap gap-2" aria-label="Supporting messages">
          {card.sources.map((id, index) => <button className={actionClass} key={id} onClick={() => onSource(id)} type="button">Source {index + 1} ↗</button>)}
        </div>}
        {card.state === "proposed" && <p className="text-xs text-muted-foreground">Draft · human review required. Approval applies to this exact revision.</p>}
        {!!card.links?.length && <div className="flex flex-wrap gap-2" aria-label="Related pages">{card.links.map((link) =>
          <button key={link.id} className={actionClass} disabled={busy} onClick={() => onAction("open", link.id)} type="button">{link.title}</button>)}</div>}
        <div className="flex flex-wrap gap-2">
          {card.document_id && <button className={actionClass} disabled={busy} onClick={() => onAction("inspect")} type="button">Inspect latest</button>}
          {card.document_id && <>
            <button className={actionClass} disabled={busy} onClick={() => onAction("changes")} type="button">Review changes</button>
            <button className={actionClass} disabled={busy} onClick={() => onAction("related")} type="button">Related knowledge</button>
            <button className={actionClass} disabled={busy} onClick={() => onAction("history")} type="button">History</button>
          </>}
          {card.state === "approved" && <button className={actionClass} disabled={busy} onClick={() => setRevising(!revising)} type="button">Propose revision</button>}
          {card.saveable && <button className={actionClass} disabled={busy} onClick={() => onAction("save")} type="button">Propose as knowledge</button>}
          {card.state === "proposed" && canReview && <>
            <button className={`${actionClass} bg-primary text-primary-foreground hover:bg-primary/90`} disabled={busy} onClick={() => onAction("approve")} type="button">Approve</button>
            <button className={actionClass} disabled={busy} onClick={() => onAction("reject")} type="button">Reject</button>
          </>}
          {card.kind === "help" && <>
            <button className={actionClass} disabled={busy} onClick={() => onAction("synthesize")} type="button">Draft from discussion</button>
            <button className={actionClass} disabled={busy} onClick={() => onAction("list")} type="button">Knowledge library</button>
            <button className={actionClass} disabled={busy} onClick={() => onAction("index")} type="button">Topic index</button>
            <button className={actionClass} disabled={busy} onClick={() => onAction("lint")} type="button">Check knowledge</button>
          </>}
          <button className={actionClass} disabled={busy} onClick={() => setAsking(!asking)} type="button">Ask a question</button>
        </div>
        {asking && <form className="flex flex-wrap gap-2" onSubmit={(event) => { event.preventDefault(); onAction("ask", question); }}>
          <input aria-label="Knowledge question" className="min-w-0 flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm" maxLength={1000} onChange={(event) => setQuestion(event.target.value)} placeholder="What do we know about…" value={question} />
          <button className={actionClass} disabled={busy || !question.trim()} type="submit">Ask</button>
        </form>}
        {revising && <form className="space-y-2" onSubmit={(event) => { event.preventDefault(); onAction("revise", replacement); }}>
          <label className="block text-sm">Proposed replacement text<textarea aria-label="Proposed replacement text" className="mt-2 w-full rounded-lg border border-border bg-background p-3 text-sm" rows={6} maxLength={12000} value={replacement} onChange={(event) => setReplacement(event.target.value)} /></label>
          <p className="text-xs text-muted-foreground">The approved version remains available until a human approves this proposal.</p>
          <button className={actionClass} disabled={busy || !replacement.trim()} type="submit">Submit revision proposal</button>
        </form>}
        {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
        <p className="text-xs text-muted-foreground">Actions and answers are posted in this Buzz channel.</p>
      </div>
    </section>
  );
}
