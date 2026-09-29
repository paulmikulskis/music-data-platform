"use client";
import ErrorPage from "./error";
import "./style.css";
import "./search.css";
import "./rooms.css";

export default function GlobalError() {
  return (
    <html lang="en">
      <body>
        <ErrorPage />
      </body>
    </html>
  );
}
