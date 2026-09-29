import { CopySQL } from "../copy-sql";

export const loginRequestLine =
  "Set up an analyst login for <name>, <work email>.";

export function LoginRequest() {
  return (
    <div className="login-request">
      <p>Send this to the data team for a personal login.</p>
      <div className="login-request-actions">
        <CopySQL sql={loginRequestLine} kind="request" />
        <a
          className="quiet"
          href={
            "mailto:?subject=" +
            encodeURIComponent("Analyst login") +
            "&body=" +
            encodeURIComponent(loginRequestLine)
          }
        >
          Email it →
        </a>
      </div>
      <code>{loginRequestLine}</code>
    </div>
  );
}
