"use client";

import { useEffect, useState } from "react";

export type ScriptStatus = "idle" | "loading" | "ready" | "error";

/** Tracks every script this hook has injected, so it is loaded at most once. */
const registry = new Map<string, ScriptStatus>();

/**
 * Load a third-party script on demand.
 *
 * Google's and Apple's sign-in SDKs are only needed on pages that offer them,
 * and both are sizeable. Loading them lazily keeps them off the landing page's
 * critical path, and the shared registry means two sign-in buttons on one page
 * do not fetch the same SDK twice.
 */
export function useScript(src: string | null | undefined): ScriptStatus {
  // Status is keyed by `src`, so a value left over from a previous script is
  // never reported for the current one.
  const [event, setEvent] = useState<{ src: string; status: ScriptStatus } | null>(
    null,
  );

  useEffect(() => {
    if (!src) return;

    const cached = registry.get(src);
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
      registry.set(src, "loading");
    }

    const onLoad = () => {
      registry.set(src, "ready");
      setEvent({ src, status: "ready" });
    };
    const onError = () => {
      registry.set(src, "error");
      setEvent({ src, status: "error" });
    };

    element.addEventListener("load", onLoad);
    element.addEventListener("error", onError);

    return () => {
      element?.removeEventListener("load", onLoad);
      element?.removeEventListener("error", onError);
    };
  }, [src]);

  if (!src) return "idle";
  const cached = registry.get(src);
  if (cached === "ready" || cached === "error") return cached;
  if (event?.src === src) return event.status;
  return "loading";
}
