"use client";
import { useEffect, useRef, type RefObject } from "react";
// A bottom sheet. Focus moves into it on open and returns to whatever opened it on close.
export function Sheet({
  title,
  close,
  children,
  className = "",
  initialFocus,
  urlKey,
}: {
  className?: string;
  urlKey?: string;
  initialFocus?: RefObject<HTMLElement | null>;
  title: string;
  close: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const closeRef = useRef(close);
  useEffect(() => {
    closeRef.current = close;
  }, [close]);
  const previousUrl = useRef<string | null>(null);
  const sheetUrl = useRef<string | null>(null);
  const dismiss = () => {
    if (previousUrl.current && window.location.href === sheetUrl.current)
      window.history.back();
    else closeRef.current();
  };
  useEffect(() => {
    const before = window.location.href;
    const url = new URL(before);
    const key = urlKey ?? title.toLowerCase().replace(/[^a-z0-9]+/g, "-");
    if (url.searchParams.get("sheet") !== key) {
      previousUrl.current = before;
      url.searchParams.set("sheet", key);
      window.history.pushState(null, "", url);
    }
    sheetUrl.current = url.href;
    const back = () => {
      if (window.location.href !== sheetUrl.current) closeRef.current();
    };
    window.addEventListener("popstate", back);
    const dialog = ref.current;
    const opener = document.activeElement;
    dialog?.showModal();
    initialFocus?.current?.focus();
    return () => {
      window.removeEventListener("popstate", back);
      if (previousUrl.current && window.location.href === sheetUrl.current)
        window.history.replaceState(null, "", previousUrl.current);
      dialog?.close();
      if (opener instanceof HTMLElement && opener.isConnected)
        opener.focus({ preventScroll: true });
    };
  }, [initialFocus, title, urlKey]);
  return (
    <dialog
      ref={ref}
      className={`sheet ${className}`}
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        dismiss();
      }}
      onClick={(event) => {
        if (event.target === ref.current) dismiss();
      }}
    >
      <div className="sheet-top">
        <span>{title}</span>
        <button onClick={dismiss} aria-label="Close">
          ×
        </button>
      </div>
      {children}
    </dialog>
  );
}
