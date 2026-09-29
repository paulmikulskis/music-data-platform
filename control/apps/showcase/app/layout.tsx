import { RateRecovery } from "../components/rate-recovery";
import type { Metadata } from "next";
import "./style.css";
import "./search.css";
import "./rooms.css";
import "./redesign.css";
import "./stack.css";
import "./link-out.css";
export const metadata: Metadata = {
  title: "Music Data Platform · Music in motion",
  robots: { index: false, follow: false },
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <RateRecovery />
        {children}
      </body>
    </html>
  );
}
