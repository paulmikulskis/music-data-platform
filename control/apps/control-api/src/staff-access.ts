import { STAFF_ANALYSIS_PATHS } from "./auth.js";

export function isOperatorRequest(method: string, path: string): boolean {
  if (path.startsWith("/actions/")) return true;
  if (method === "GET") return false;
  return (
    /^\/(?:sandbox|sandboxes)(?:\/|$)/.test(path) ||
    (path.startsWith("/api/") && !STAFF_ANALYSIS_PATHS.has(path.slice(4)))
  );
}

// Presentation only. The HTTP and RPC guards enforce operator permissions.
export function disableOperatorForms(html: string): string {
  return html.replace(
    /(<form\b[^>]*>)([\s\S]*?)(<\/form>)/gi,
    (form, opening: string, body: string, closing: string) => {
      if (
        !/method="post"/i.test(opening) ||
        !/action="\/actions\//i.test(opening)
      )
        return form;
      return `${opening}<p>This action needs the admin role. Ask an operator; <a href="/runbooks/forbidden">open the access runbook</a>.</p><fieldset disabled style="display:contents">${body}</fieldset>${closing}`;
    },
  );
}
