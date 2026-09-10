export type KnowledgeCard = {
  version: 1;
  kind: "help" | "proposal" | "document" | "answer";
  channel_id: string;
  title: string;
  summary: string;
  sources: string[];
  document_id?: string;
  revision?: string;
  state?: string;
  links?: { id: string; title: string }[];
  saveable?: boolean;
};

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const hex = /^[0-9a-f]{64}$/;
const states = new Set(["proposed", "approved", "rejected", "archived", "superseded"]);

export function parseKnowledgeCard(
  message: { kind?: number; signerPubkey?: string; tags?: string[][]; pending?: boolean; edited?: boolean },
  channelId: string | null,
  trustedSigner: string | undefined,
): KnowledgeCard | null {
  if (!trustedSigner || !hex.test(trustedSigner) || message.signerPubkey !== trustedSigner ||
      message.pending || message.edited || !channelId || ![9, 40002].includes(message.kind ?? 0)) return null;
  const tags = message.tags ?? [];
  const bindings = tags.filter((tag) => tag[0] === "h");
  const payloads = tags.filter((tag) => tag[0] === "gcor-card");
  if (bindings.length !== 1 || bindings[0][1] !== channelId || payloads.length !== 1 ||
      payloads[0].length !== 2 || payloads[0][1].length > 16000) return null;
  try {
    const card = JSON.parse(payloads[0][1]);
    if (!card || card.version !== 1 || !["help", "proposal", "document", "answer"].includes(card.kind) ||
        card.channel_id !== channelId || !uuid.test(card.channel_id) ||
        typeof card.title !== "string" || !card.title.trim() || card.title.length > 300 ||
        typeof card.summary !== "string" || card.summary.length > 7000 ||
        !Array.isArray(card.sources) || card.sources.length > 6 ||
        !card.sources.every((id: unknown) => typeof id === "string" && hex.test(id))) return null;
    if (["proposal", "document"].includes(card.kind) &&
        (typeof card.document_id !== "string" || !uuid.test(card.document_id) ||
         typeof card.revision !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/.test(card.revision) ||
         !Number.isFinite(Date.parse(card.revision)) || !states.has(card.state))) return null;
    if (card.links !== undefined && (!Array.isArray(card.links) || card.links.length > 6 ||
        !card.links.every((link: { id?: unknown; title?: unknown }) => link && typeof link.id === "string" && uuid.test(link.id) && typeof link.title === "string" && link.title.length <= 300))) return null;
    // Return only supported fields; message data cannot provide executable actions or URLs.
    return { version: 1, kind: card.kind, channel_id: channelId, title: card.title,
      summary: card.summary, sources: card.sources,
      document_id: ["proposal", "document"].includes(card.kind) ? card.document_id : undefined,
      revision: ["proposal", "document"].includes(card.kind) ? card.revision : undefined,
      state: ["proposal", "document"].includes(card.kind) ? card.state : undefined,
      links: card.links?.map((link: { id: string; title: string }) => ({ id: link.id, title: link.title })),
      saveable: card.kind === "answer" && card.saveable === true };
  } catch { return null; }
}

export function knowledgeCommand(card: KnowledgeCard, action: string, question = ""): string {
  if (action === "ask" && question.trim() && question.length <= 1000) return `!knowledge ask ${question.trim()}`;
  if (["list", "synthesize", "index", "lint"].includes(action)) return `!knowledge ${action}`;
  if (action === "open" && card.links?.some((link) => link.id === question)) return `!knowledge show ${question}`;
  if (action === "save" && card.saveable && hex.test(question)) return `!knowledge save ${question}`;
  if (card.document_id && ["changes", "history", "related"].includes(action)) return `!knowledge ${action} ${card.document_id}`;
  if (action === "revise" && card.document_id && card.revision && card.state === "approved" && question.trim() && question.length <= 12000)
    return `!knowledge revise ${card.document_id} ${card.revision} | ${question.trim()}`;
  if (card.document_id && action === "inspect") return `!knowledge show ${card.document_id}`;
  if (card.document_id && card.revision && ["approve", "reject"].includes(action) && card.state === "proposed") {
    return `!knowledge ${action} ${card.document_id} ${card.revision}`;
  }
  throw new Error("This action is not available for this knowledge card.");
}
