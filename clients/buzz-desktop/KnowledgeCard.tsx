import * as React from "react";
import { useAppNavigation } from "@/app/navigation/useAppNavigation";
import { useChannelMembersQuery } from "@/features/channels/hooks";
import { useSendMessageMutation } from "@/features/messages/hooks";
import { useIdentityQuery } from "@/shared/api/hooks";
import { KnowledgeCardView } from "./KnowledgeCardView";
import { knowledgeCommand, type KnowledgeCard as Card } from "./knowledgeCardModel";

export function KnowledgeCard({ card, messageId }: { card: Card; messageId: string }) {
  const identity = useIdentityQuery();
  const members = useChannelMembersQuery(card.channel_id);
  const send = useSendMessageMutation(null, identity.data);
  const { goChannel } = useAppNavigation();
  const [notice, setNotice] = React.useState("");
  const inFlight = React.useRef(false);
  const [reviewSent, setReviewSent] = React.useState(false);
  const member = members.data?.find((entry) => entry.pubkey === identity.data?.pubkey);
  const canReview = !reviewSent && member && !member.isAgent && ["owner", "admin"].includes(member.role);
  async function act(action: string, question?: string) {
    if (inFlight.current || !identity.data) return;
    inFlight.current = true;
    setNotice("");
    try {
      await send.mutateAsync({ channelId: card.channel_id, content: knowledgeCommand(card, action, action === "save" ? messageId : question),
        parentEventId: action === "synthesize" ? null : messageId });
      if (["approve", "reject"].includes(action)) setReviewSent(true);
      setNotice("Request sent. The Knowledge agent will confirm the result in this conversation.");
    } catch { setNotice("Could not send this action. Check your connection and try again."); }
    finally { inFlight.current = false; }
  }
  return <KnowledgeCardView card={card} busy={send.isPending || !member} canReview={!!canReview} notice={notice}
    onAction={(action, question) => { void act(action, question); }}
    onSource={(id) => { void goChannel(card.channel_id, { messageId: id }); }} />;
}
