"use client";
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
  type SyntheticEvent,
} from "react";
import { createPortal } from "react-dom";
import { motion, useReducedMotion } from "motion/react";
import { Sheet } from "./sheet";
const focusable =
  'a[href],button:not([disabled]),[tabindex]:not([tabindex="-1"])';
// One hover card. A mouse hover or keyboard focus opens it beside the trigger, Tab moves into it and
// Escape closes it. On a phone, press and hold opens it as a bottom sheet. With tap, a click, Enter
// or Space opens the sheet too, for triggers that have no action of their own. Cards with links
// are dialogs; text-only cards are tooltips that describe their trigger.
export function Hover({
  label,
  card,
  children,
  className = "",
  tap = false,
  interactive = false,
  fitViewport = false,
}: {
  label: string;
  card: ReactNode;
  children: ReactNode;
  className?: string;
  tap?: boolean;
  interactive?: boolean;
  fitViewport?: boolean;
}) {
  const trigger = useRef<HTMLSpanElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const held = useRef(false);
  const sheetBox = useRef<HTMLSpanElement>(null);
  const cardBox = useRef<HTMLDivElement>(null);
  const id = useId();
  const [place, setPlace] = useState<{
    left: number;
    top?: number;
    bottom?: number;
    width?: number;
    maxHeight?: number;
  } | null>(null);
  // Inside an open sheet the card joins the sheet's top layer, or the sheet would hide it.
  const [host, setHost] = useState<Element | null>(null);
  const [sheet, setPanel] = useState(false);
  const reduced = useReducedMotion();
  // Events from this trigger's own card or sheet bubble through React; they are not the trigger's.
  const inside = (target: EventTarget | null) =>
    target instanceof Node &&
    !!(sheetBox.current?.contains(target) || cardBox.current?.contains(target));
  const own = (event: SyntheticEvent) => !inside(event.target);
  const show = useCallback(() => {
    const box = trigger.current?.getBoundingClientRect();
    if (!box) return;
    if (fitViewport) {
      const zoom = Number(getComputedStyle(document.documentElement).zoom) || 1;
      const width = Math.min(280, (window.innerWidth - 24) / zoom);
      const below = window.innerHeight - box.bottom - 22;
      const above = box.top - 22;
      const useAbove = below < 220 * zoom && above > below;
      setHost(trigger.current?.closest("dialog[open]") ?? document.body);
      setPlace({
        left:
          Math.max(
            12,
            Math.min(box.left, window.innerWidth - width * zoom - 12),
          ) / zoom,
        width,
        maxHeight: Math.max(44, useAbove ? above : below) / zoom,
        ...(useAbove
          ? { bottom: (window.innerHeight - box.top + 10) / zoom }
          : { top: (box.bottom + 10) / zoom }),
      });
      return;
    }
    // Open above the trigger when there is no room below it.
    const above = box.bottom + 220 > window.innerHeight && box.top > 220;
    const left = Math.min(Math.max(12, box.left), window.innerWidth - 292);
    setHost(trigger.current?.closest("dialog[open]") ?? document.body);
    setPlace(
      above
        ? { left, bottom: window.innerHeight - box.top + 10 }
        : { left, top: box.bottom + 10 },
    );
  }, [fitViewport]);
  const hide = (refocus = false) => {
    setPlace(null);
    if (refocus)
      trigger.current?.querySelector<HTMLElement>(focusable)?.focus();
  };
  const soon = (action: () => void, delay: number) => {
    clearTimeout(timer.current);
    timer.current = setTimeout(action, delay);
  };
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    if (!place) return;
    const onScroll = () => {
      // Scroll anchoring can move the page while its tile stays under the pointer.
      // Keep a fitted card beside that tile until the pointer leaves it.
      if (
        fitViewport &&
        (trigger.current?.matches(":hover, :focus-within") ||
          cardBox.current?.matches(":hover, :focus-within"))
      ) {
        show();
        return;
      }
      setPlace(null);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [place, fitViewport, show]);
  // Keep the card and sheet's clicks, taps and keys from reaching a link or button around the trigger.
  const contain = (event: SyntheticEvent) => {
    if (!own(event)) event.stopPropagation();
  };
  return (
    <span
      ref={trigger}
      className={`hover-trigger ${className}`}
      aria-describedby={place && !interactive ? id : undefined}
      onPointerEnter={(event) => {
        if (event.pointerType === "mouse" && own(event)) soon(show, 90);
      }}
      onPointerLeave={(event) => {
        if (event.pointerType === "mouse" && own(event))
          soon(() => setPlace(null), 120);
      }}
      onPointerDown={(event) => {
        contain(event);
        if (!own(event)) return;
        held.current = false;
        if (event.pointerType !== "mouse")
          soon(() => {
            held.current = true;
            setPlace(null);
            setPanel(true);
          }, 450);
      }}
      onPointerUp={(event) => {
        contain(event);
        clearTimeout(timer.current);
      }}
      onPointerCancel={() => clearTimeout(timer.current)}
      onContextMenu={(event) => {
        if (held.current) event.preventDefault();
      }}
      onClickCapture={(event) => {
        if (held.current && own(event)) {
          held.current = false;
          event.preventDefault();
          event.stopPropagation();
        }
      }}
      onClick={(event) => {
        contain(event);
        if (
          tap &&
          own(event) &&
          !(event.target instanceof Element && event.target.closest("a"))
        ) {
          setPlace(null);
          setPanel(true);
        }
      }}
      onFocus={(event) => {
        if (
          own(event) &&
          event.target instanceof Element &&
          event.target.matches(":focus-visible")
        )
          show();
      }}
      onBlur={(event) => {
        if (own(event) && !inside(event.relatedTarget)) setPlace(null);
      }}
      onKeyDown={(event) => {
        contain(event);
        if (!own(event)) return;
        if (event.key === "Escape") hide();
        const first = cardBox.current?.querySelector<HTMLElement>(focusable);
        if (event.key === "Tab" && !event.shiftKey && place && first) {
          event.preventDefault();
          first.focus();
        }
      }}
    >
      {children}
      {place &&
        host &&
        createPortal(
          <motion.div
            ref={cardBox}
            id={id}
            className="hover-card"
            data-popover
            role={interactive ? "dialog" : "tooltip"}
            aria-label={label}
            style={place}
            initial={reduced ? false : { opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.12 }}
            onPointerEnter={() => clearTimeout(timer.current)}
            onPointerLeave={() => soon(() => setPlace(null), 120)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.stopPropagation();
                hide(true);
              }
            }}
            onBlur={(event) => {
              const next = event.relatedTarget;
              if (
                !inside(next) &&
                !(next instanceof Node && trigger.current?.contains(next))
              )
                setPlace(null);
            }}
          >
            {card}
          </motion.div>,
          host,
        )}
      {sheet &&
        createPortal(
          <span ref={sheetBox}>
            <Sheet title={label} close={() => setPanel(false)}>
              <div data-popover>{card}</div>
            </Sheet>
          </span>,
          document.body,
        )}
    </span>
  );
}
