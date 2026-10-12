"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { useEffect, useState } from "react";
import { Toaster } from "sonner";

import { ApiError } from "@/lib/api";
import { useAuthStore } from "@/store/auth";

/**
 * Create the query client inside the component tree.
 *
 * A module-level client would be shared across requests on the server and leak
 * one user's cached data into another's page.
 */
function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Travel data changes slowly; a minute of staleness saves a lot of
        // refetching as the user moves between pages.
        staleTime: 60_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        retry(failureCount, error) {
          // Never retry a 401/403/404 — the answer will not change, and
          // retrying an auth failure just delays the sign-in prompt.
          if (error instanceof ApiError && !error.isRetryable) return false;
          return failureCount < 2;
        },
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
      },
      mutations: {
        retry: false,
      },
    },
  });
}

function SessionBootstrap({ children }: { children: React.ReactNode }) {
  const bootstrap = useAuthStore((state) => state.bootstrap);

  useEffect(() => {
    void bootstrap();

    // Another tab signing in or out should update this one too.
    const onChange = () => void bootstrap();
    window.addEventListener("auth:changed", onChange);
    window.addEventListener("storage", onChange);
    return () => {
      window.removeEventListener("auth:changed", onChange);
      window.removeEventListener("storage", onChange);
    };
  }, [bootstrap]);

  return <>{children}</>;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(makeQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider
        attribute="class"
        defaultTheme="system"
        enableSystem
        disableTransitionOnChange
      >
        <SessionBootstrap>{children}</SessionBootstrap>
        <Toaster
          position="top-center"
          richColors
          closeButton
          toastOptions={{
            classNames: {
              toast:
                "rounded-xl border border-border bg-card text-card-foreground shadow-lg",
            },
          }}
        />
      </ThemeProvider>
    </QueryClientProvider>
  );
}
