"use client";

import { Loader2, LockKeyhole } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Button } from "@/components/ui/button";
import { useAuthStore } from "@/store/auth";

/**
 * Gate a page behind a session.
 *
 * Client-side rather than middleware, because the session lives in
 * localStorage — the server has no cookie to check. Nothing sensitive leaks:
 * the data itself is fetched from an API that enforces the same rule, so this
 * only saves the user a wasted request and a confusing empty screen.
 *
 * While the session is still being restored it renders a spinner rather than
 * the sign-in prompt, which would otherwise flash on every reload for a user
 * who is, in fact, signed in.
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuthStore();
  const pathname = usePathname();

  if (isLoading) {
    return (
      <div className="flex min-h-[60svh] items-center justify-center">
        <Loader2 className="size-7 animate-spin text-muted-foreground" />
        <span className="sr-only">Checking your session</span>
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <div className="mx-auto flex min-h-[60svh] max-w-md flex-col items-center justify-center px-4 text-center">
        <span className="flex size-14 items-center justify-center rounded-2xl bg-primary-soft text-primary">
          <LockKeyhole className="size-6" />
        </span>
        <h1 className="mt-6 text-2xl font-bold">Sign in to see this</h1>
        <p className="mt-2 text-muted-foreground">
          Your trips are tied to your account, so we need to know who you are.
        </p>
        <div className="mt-7 flex flex-col gap-3 sm:flex-row">
          <Button asChild size="lg">
            {/* Round-trip back to where they were headed. */}
            <Link href={`/sign-in?next=${encodeURIComponent(pathname)}`}>
              Sign in
            </Link>
          </Button>
          <Button asChild size="lg" variant="outline">
            <Link href={`/sign-up?next=${encodeURIComponent(pathname)}`}>
              Create an account
            </Link>
          </Button>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
