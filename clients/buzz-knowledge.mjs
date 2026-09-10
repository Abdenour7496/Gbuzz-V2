/** Call the Buzz knowledge reader with an existing Nostr signEvent function.
 * Private keys stay in the caller's existing signer/wallet.
 */
export async function askBuzzKnowledge({ origin = "http://127.0.0.1:5011", channelId, query, signEvent }) {
  return signedBuzzRequest({ origin, path: '/api/ask', payload: { channel_id: channelId, query }, signEvent });
}

export async function signedBuzzRequest({ origin = "http://127.0.0.1:5011", path, payload: data, signEvent }) {
  if (typeof signEvent !== "function") throw new Error("An existing Nostr signer is required");
  const url = new URL(path, origin).href;
  if (new URL(url).origin !== new URL(origin).origin) throw new Error('Request must stay on the knowledge origin');
  const body = JSON.stringify(data);
  const bytes = new TextEncoder().encode(body);
  const payload = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    byte => byte.toString(16).padStart(2, "0")).join("");
  const event = await signEvent({ kind: 27235, created_at: Math.floor(Date.now() / 1000),
    content: "", tags: [["u", url], ["method", "POST"], ["payload", payload]] });
  const encoded = btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(event))));
  const response = await fetch(url, { method: "POST", headers: {
    "Content-Type": "application/json", Authorization: `Nostr ${encoded}` }, body });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : `Knowledge request failed (${response.status})`);
  }
  return response.json();
}
