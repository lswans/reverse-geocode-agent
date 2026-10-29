// Links in an agent report are chosen by the model, which reads untrusted web
// pages, so treat them as hostile. HTML escaping stops markup injection but not
// a `javascript:` or `data:` URL in an href. Only absolute http(s) URLs pass.

const ALLOWED_PROTOCOLS = new Set(['http:', 'https:']);

export function safeHttpUrl(value) {
  if (typeof value !== 'string' || value.trim() === '') return null;
  let parsed;
  try {
    // No base URL: relative links are refused rather than resolved against this page.
    parsed = new URL(value.trim());
  } catch {
    return null;
  }
  if (!ALLOWED_PROTOCOLS.has(parsed.protocol) || !parsed.hostname) return null;
  return parsed.href;
}
