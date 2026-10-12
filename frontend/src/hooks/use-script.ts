"use client";

import { useEffect, useSyncExternalStore } from "react";

export type ScriptStatus = "idle" | "loading" | "ready" | "error";

/**
 * Load status for every script this hook has injected, shared process-wide.
 *
 * This is genuinely external state — two components asking for the same SDK must
 * see the same status — so it is exposed as a store and read with
 * `useSyncExternalStore` rather than mirrored into component state inside an
 * effect. Copying it into state would mean a render where the hook reports
 * "loading" for a script that has already finished.
 */
const statuses = new Map<string, ScriptStatus>();
const listeners = new Set<() => void>();

function setStatus(src: string, status: ScriptStatus) {
  if (statuses.get(src) === status) return;
  statuses.set(src, status);
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Load a third-party script on demand.
 *
 * Google's and Apple's sign-in SDKs are only needed on pages that offer them,
 * and both are sizeable. Loading them lazily keeps them off the landing page's
 * critical path, and the shared registry means two sign-in buttons on one page
 * do not fetch the same SDK twice.
 */
export function useScript(src: string | null | undefined): ScriptStatus {
  const status = useSyncExternalStore(
    subscribe,
    () => (src ? (statuses.get(src) ?? "loading") : "idle"),
    // There is no script loading on the server, so nothing is ever ready there.
    () => (src ? "loading" : "idle"),
  );

  useEffect(() => {
    if (!src) return;

    const cached = statuses.get(src);
    if (cached === "ready" || cached === "error") return;

    let element = document.querySelector<HTMLScriptElement>(
      `script[src="${src}"]`,
    );

    if (!element) {
      element = document.createElement("script");
      element.src = src;
      element.async = true;
      element.defer = true;
      document.head.appendChild(element);
      setStatus(src, "loading");
    }

    // Both run as events, long after render, so updating the store here is safe.
    const onLoad = () => setStatus(src, "ready");
    const onError = () => setStatus(src, "error");

    element.addEventListener("load", onLoad);
    element.addEventListener("error", onError);

    return () => {
      element?.removeEventListener("load", onLoad);
      element?.removeEventListener("error", onError);
    };
  }, [src]);

  return status;
}
