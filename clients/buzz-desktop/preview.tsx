import * as React from "react";
import { createRoot } from "react-dom/client";
import { KnowledgeCardView } from "./src/features/messages/ui/KnowledgeCardView";
import "./src/shared/styles/globals.css";

function Preview() {
  const [notice,setNotice]=React.useState("");
  const card={version:1 as const,kind:"proposal" as const,channel_id:"11111111-1111-4111-8111-111111111111",title:"Weekly recovery drills",state:"proposed",document_id:"11111111-1111-4111-8111-111111111111",revision:"2026-09-08T12:00:00Z",sources:["ab".repeat(32)],summary:"The team proposes a weekly restore exercise.\n\n• Restore a sample of approved knowledge.\n• Verify artifact checksums.\n• Record results and assign follow-up actions.\n\nOpen question: who will own the first drill?"};
  return <main className="mx-auto max-w-3xl p-6"><p className="mb-5 text-sm text-muted-foreground">Buzz Desktop · Knowledge card preview · sample data</p>
    <KnowledgeCardView card={card} busy={false} canReview notice={notice} onSource={()=>setNotice("Preview: opens the supporting Buzz message.")} onAction={(action)=>setNotice(`Preview: ${action} sends your signed Buzz message.`)}/>
    <KnowledgeCardView card={{...card,kind:"help",title:"Build knowledge together",state:undefined,document_id:undefined,sources:[],summary:""}} busy={false} canReview={false} notice="" onSource={()=>{}} onAction={(action)=>setNotice(`Preview: ${action}`)}/>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Preview/>);
