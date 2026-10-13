"use client";

/**
 * Google and Apple sign-in.
 *
 * Both follow the same shape: the provider's SDK runs the sign-in in a popup and
 * hands back a signed **ID token**, which we post to our API. The server — not
 * the browser — verifies that token's signature, so nothing here needs to be
 * trusted. See `backend/app/core/oauth.py`.
 *
 * A **nonce** is generated per attempt and passed to the provider, which embeds
 * it in the token. The server compares the two, which is what stops a token
 * obtained somewhere else being replayed into our sign-in.
 *
 * Branding is deliberately *not* customised beyond layout. Google requires its
 * own rendered button, and Apple's guidelines fix the mark, wording and
 * proportions. A hand-rolled "G" button is both off-guideline and less
 * trustworthy to the people who recognise the real one.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";

import { useScript } from "@/hooks/use-script";
import { ApiError } from "@/lib/api";
import { cn, randomNonce } from "@/lib/utils";
import { useAuthStore } from "@/store/auth";

const GOOGLE_SDK = "https://accounts.google.com/gsi/client";
const APPLE_SDK =
  "https://appleid.cdn-apple.com/appleauth/static/jsapi/appleid/1/en_US/appleid.auth.js";

const GOOGLE_CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;
const APPLE_CLIENT_ID = process.env.NEXT_PUBLIC_APPLE_CLIENT_ID;

// ---------------------------------------------------------------------------
// Minimal typings for the provider SDKs
// ---------------------------------------------------------------------------

interface GoogleCredentialResponse {
  credential: string;
}

interface AppleSignInResponse {
  authorization: { id_token: string; code: string; state?: string };
  user?: { name?: { firstName?: string; lastName?: string }; email?: string };
}

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: Record<string, unknown>) => void;
          renderButton: (
            parent: HTMLElement,
            options: Record<string, unknown>,
          ) => void;
          cancel: () => void;
        };
      };
    };
    AppleID?: {
      auth: {
        init: (config: Record<string, unknown>) => void;
        signIn: () => Promise<AppleSignInResponse>;
      };
    };
  }
}

export interface SocialButtonProps {
  /** Where to send the user once signed in. */
  onSuccess?: () => void;
  /** Changes the wording between the sign-in and sign-up pages. */
  mode?: "signin" | "signup";
  className?: string;
}

/** Turn any sign-in failure into a message worth reading. */
function reportFailure(error: unknown, provider: string) {
  if (error instanceof ApiError) {
    toast.error(error.message);
    return;
  }
  // The provider SDKs reject with a plain object when the user closes the popup,
  // which is not an error worth shouting about.
  const message = String((error as { error?: string })?.error ?? "");
  if (message.includes("popup_closed") || message.includes("user_cancelled")) {
    return;
  }
  toast.error(`Could not finish signing in with ${provider}. Please try again.`);
}

// ---------------------------------------------------------------------------
// Google
// ---------------------------------------------------------------------------

export function GoogleSignInButton({
  onSuccess,
  mode = "signin",
  className,
}: SocialButtonProps) {
  const status = useScript(GOOGLE_CLIENT_ID ? GOOGLE_SDK : null);
  const containerRef = useRef<HTMLDivElement>(null);
  const signInWithProvider = useAuthStore((s) => s.signInWithProvider);

  // Kept in a ref so the SDK callback, which is registered once, always sees
  // the current nonce rather than the one from first render.
  const nonceRef = useRef<string>("");
  const [width, setWidth] = useState(360);

  const handleCredential = useCallback(
    async (response: GoogleCredentialResponse) => {
      try {
        await signInWithProvider("google", {
          id_token: response.credential,
          nonce: nonceRef.current || undefined,
        });
        toast.success("Signed in with Google.");
        onSuccess?.();
      } catch (error) {
        reportFailure(error, "Google");
      }
    },
    [onSuccess, signInWithProvider],
  );

  // Google's button is rendered at a fixed pixel width, so it has to be told
  // how wide its container is — and re-told when the viewport changes.
  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      const next = Math.round(entry.contentRect.width);
      if (next > 0) setWidth(Math.min(400, Math.max(200, next)));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (status !== "ready" || !window.google || !containerRef.current) return;

    nonceRef.current = randomNonce();
    window.google.accounts.id.initialize({
      client_id: GOOGLE_CLIENT_ID,
      callback: handleCredential,
      nonce: nonceRef.current,
      // One Tap is switched off here: an automatic prompt on a sign-in page the
      // user navigated to deliberately is redundant and often mis-clicked.
      auto_select: false,
      cancel_on_tap_outside: true,
      use_fedcm_for_prompt: true,
    });

    containerRef.current.innerHTML = "";
    window.google.accounts.id.renderButton(containerRef.current, {
      type: "standard",
      theme: "outline",
      size: "large",
      shape: "pill",
      text: mode === "signup" ? "signup_with" : "signin_with",
      logo_alignment: "center",
      width,
    });

    return () => window.google?.accounts.id.cancel();
  }, [status, handleCredential, mode, width]);

  if (!GOOGLE_CLIENT_ID) return null;

  return (
    <div className={cn("w-full", className)}>
      {status === "error" ? (
        <p className="rounded-xl border border-border px-4 py-3 text-center text-sm text-muted-foreground">
          Google sign-in could not load. Check your connection or use your email.
        </p>
      ) : (
        <div
          ref={containerRef}
          className={cn(
            "flex min-h-11 w-full items-center justify-center overflow-hidden rounded-full",
            status !== "ready" && "skeleton",
          )}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Apple
// ---------------------------------------------------------------------------

export function AppleSignInButton({
  onSuccess,
  mode = "signin",
  className,
}: SocialButtonProps) {
  const status = useScript(APPLE_CLIENT_ID ? APPLE_SDK : null);
  const signInWithProvider = useAuthStore((s) => s.signInWithProvider);
  const [isBusy, setIsBusy] = useState(false);
  const nonceRef = useRef<string>("");
  const labelId = useId();

  useEffect(() => {
    if (status !== "ready" || !window.AppleID || !APPLE_CLIENT_ID) return;
    nonceRef.current = randomNonce();
    window.AppleID.auth.init({
      clientId: APPLE_CLIENT_ID,
      scope: "name email",
      redirectURI:
        process.env.NEXT_PUBLIC_APPLE_REDIRECT_URI ??
        (typeof window !== "undefined" ? window.location.origin : ""),
      nonce: nonceRef.current,
      usePopup: true,
    });
  }, [status]);

  const handleClick = useCallback(async () => {
    if (!window.AppleID) return;
    setIsBusy(true);
    try {
      const response = await window.AppleID.auth.signIn();
      // Apple returns the name only on the very first authorisation, and only
      // outside the token — so it has to be forwarded separately or it is lost.
      const name = response.user?.name;
      const fullName = name
        ? [name.firstName, name.lastName].filter(Boolean).join(" ")
        : undefined;

      await signInWithProvider("apple", {
        id_token: response.authorization.id_token,
        nonce: nonceRef.current || undefined,
        full_name: fullName || undefined,
      });
      toast.success("Signed in with Apple.");
      onSuccess?.();
    } catch (error) {
      reportFailure(error, "Apple");
    } finally {
      setIsBusy(false);
    }
  }, [onSuccess, signInWithProvider]);

  if (!APPLE_CLIENT_ID) return null;

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={status !== "ready" || isBusy}
      aria-labelledby={labelId}
      className={cn(
        // Apple's guidelines fix the black fill, the white mark and the wording.
        "flex h-11 w-full items-center justify-center gap-2.5 rounded-full bg-black px-5 text-sm font-medium text-white",
        "transition-all hover:bg-black/85 active:scale-[0.98]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        "disabled:cursor-not-allowed disabled:opacity-60",
        "dark:bg-white dark:text-black dark:hover:bg-white/90",
        className,
      )}
    >
      <svg viewBox="0 0 24 24" className="size-[18px] fill-current" aria-hidden>
        <path d="M17.05 12.53c-.02-2.2 1.8-3.26 1.88-3.31-1.02-1.5-2.62-1.7-3.18-1.73-1.35-.14-2.64.8-3.33.8-.69 0-1.75-.78-2.87-.76-1.48.02-2.84.86-3.6 2.18-1.54 2.67-.39 6.62 1.1 8.79.73 1.06 1.6 2.25 2.75 2.2 1.1-.04 1.52-.71 2.85-.71 1.33 0 1.71.71 2.87.69 1.19-.02 1.94-1.08 2.66-2.14.84-1.23 1.19-2.42 1.21-2.48-.03-.01-2.32-.89-2.34-3.53zM14.9 5.6c.61-.74 1.02-1.77.91-2.8-.88.04-1.94.59-2.57 1.32-.56.65-1.05 1.7-.92 2.7.98.08 1.98-.5 2.58-1.22z" />
      </svg>
      <span id={labelId}>
        {isBusy
          ? "Signing in…"
          : mode === "signup"
            ? "Sign up with Apple"
            : "Sign in with Apple"}
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Both together
// ---------------------------------------------------------------------------

/**
 * Renders whichever providers are configured, with a divider.
 *
 * Nothing renders at all when neither is set up, so a self-hosted deployment
 * without OAuth keys shows a clean email-only form rather than dead buttons.
 */
export function SocialSignIn({ onSuccess, mode = "signin" }: SocialButtonProps) {
  const hasGoogle = Boolean(GOOGLE_CLIENT_ID);
  const hasApple = Boolean(APPLE_CLIENT_ID);
  if (!hasGoogle && !hasApple) return null;

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-3">
        <GoogleSignInButton onSuccess={onSuccess} mode={mode} />
        <AppleSignInButton onSuccess={onSuccess} mode={mode} />
      </div>
      <div className="flex items-center gap-4 pt-1">
        <span className="h-px flex-1 bg-border" />
        <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          or continue with email
        </span>
        <span className="h-px flex-1 bg-border" />
      </div>
    </div>
  );
}

/** True when at least one federated provider is configured in this build. */
export const hasSocialSignIn = Boolean(GOOGLE_CLIENT_ID || APPLE_CLIENT_ID);
