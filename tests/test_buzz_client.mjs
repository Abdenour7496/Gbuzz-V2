import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { askBuzzKnowledge } from "../clients/buzz-knowledge.mjs";

test("signs exactly the URL and UTF-8 bytes sent to the knowledge reader", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (url, options) => {
      const event = JSON.parse(Buffer.from(options.headers.Authorization.slice(6), "base64").toString("utf8"));
      assert.equal(event.kind, 27235);
      assert.deepEqual(event.tags.slice(0, 3), [["u", url], ["method", "POST"],
        ["payload", createHash("sha256").update(options.body, "utf8").digest("hex")]]);
      assert.equal(event.tags[3][0], "nonce");
      assert.ok(event.tags[3][1]);
      assert.equal(JSON.parse(options.body).query, "Résumé? 日本語");
      return { ok: true, json: async () => ({ answer: "ok" }) };
    };
    assert.deepEqual(await askBuzzKnowledge({ channelId: "channel", query: "Résumé? 日本語",
      signEvent: async event => ({ ...event, pubkey: "signer", sig: "test" }) }), { answer: "ok" });
  } finally { globalThis.fetch = original; }
});

test("requires an explicit existing signer", async () => {
  await assert.rejects(askBuzzKnowledge({ channelId: "channel", query: "q" }), /signer/);
});

test("retries sign a fresh proof without changing the request body", async () => {
  const original = globalThis.fetch;
  const events = [];
  const bodies = [];
  try {
    globalThis.fetch = async (_url, options) => {
      bodies.push(options.body);
      return { ok: true, json: async () => ({}) };
    };
    const request = { channelId: "channel", query: "same",
      signEvent: async event => { events.push(event); return event; } };
    await askBuzzKnowledge(request);
    await askBuzzKnowledge(request);
    assert.equal(bodies[0], bodies[1]);
    assert.notEqual(events[0].tags[3][1], events[1].tags[3][1]);
  } finally { globalThis.fetch = original; }
});
