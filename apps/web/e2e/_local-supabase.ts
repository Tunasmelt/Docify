// Mirrors apps/api/tests/_local_supabase.py — these are the Supabase CLI's
// fixed local-dev demo keys (provisioned by every `supabase start`), not
// secret. Used only to clean up test users created against the local stack
// during these e2e runs; must never point at the live project.

export const LOCAL_SUPABASE_URL = "http://127.0.0.1:54321";
export const LOCAL_SUPABASE_SERVICE_ROLE_KEY =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." +
  "eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImV4cCI6MTk4MzgxMjk5Nn0." +
  "EGIM96RAZx35lJzdJsyH-qQwv8Hdp7fsn3W0YpN81IU";

export async function deleteTestUserByEmail(email: string): Promise<void> {
  const listRes = await fetch(
    `${LOCAL_SUPABASE_URL}/auth/v1/admin/users?email=${encodeURIComponent(email)}`,
    {
      headers: {
        apikey: LOCAL_SUPABASE_SERVICE_ROLE_KEY,
        Authorization: `Bearer ${LOCAL_SUPABASE_SERVICE_ROLE_KEY}`,
      },
    }
  );
  if (!listRes.ok) return;
  const { users } = (await listRes.json()) as { users: { id: string }[] };
  for (const user of users) {
    await fetch(`${LOCAL_SUPABASE_URL}/auth/v1/admin/users/${user.id}`, {
      method: "DELETE",
      headers: {
        apikey: LOCAL_SUPABASE_SERVICE_ROLE_KEY,
        Authorization: `Bearer ${LOCAL_SUPABASE_SERVICE_ROLE_KEY}`,
      },
    });
  }
}

export async function createTestUser(
  email: string,
  password: string
): Promise<string> {
  const res = await fetch(`${LOCAL_SUPABASE_URL}/auth/v1/admin/users`, {
    method: "POST",
    headers: {
      apikey: LOCAL_SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${LOCAL_SUPABASE_SERVICE_ROLE_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ email, password, email_confirm: true }),
  });
  if (!res.ok) {
    throw new Error(`createTestUser failed: ${res.status} ${await res.text()}`);
  }
  const { id } = (await res.json()) as { id: string };
  return id;
}

// The local stack's mail catcher is Mailpit (confirmed live during the
// FEAT-013 auth self-audit) — the `INBUCKET_URL`/`MAILPIT_URL` printed by
// `supabase status` point at the same host:port; only Mailpit's own REST API
// (/api/v1/messages) actually works against it, not classic Inbucket's.
export const LOCAL_MAILPIT_URL = "http://127.0.0.1:54324";

interface MailpitMessageSummary {
  ID: string;
  To: { Address: string }[];
}

interface MailpitMessagesResponse {
  messages: MailpitMessageSummary[];
}

interface MailpitMessage {
  Text: string;
}

/** Polls Mailpit for the most recent message to `email` and returns the
 * first http(s) link found in its plain-text body — used to pick up real
 * password-reset/confirmation links a real GoTrue send actually produced. */
export async function waitForEmailLink(
  email: string,
  { timeoutMs = 8000, intervalMs = 300 }: { timeoutMs?: number; intervalMs?: number } = {}
): Promise<string | null> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const listRes = await fetch(`${LOCAL_MAILPIT_URL}/api/v1/messages`);
    if (listRes.ok) {
      const data = (await listRes.json()) as MailpitMessagesResponse;
      const match = data.messages.find((m) => m.To.some((t) => t.Address === email));
      if (match) {
        const msgRes = await fetch(`${LOCAL_MAILPIT_URL}/api/v1/message/${match.ID}`);
        if (msgRes.ok) {
          const full = (await msgRes.json()) as MailpitMessage;
          const linkMatch = full.Text.match(/https?:\/\/[^\s)]+/);
          if (linkMatch) return linkMatch[0];
        }
      }
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  return null;
}
