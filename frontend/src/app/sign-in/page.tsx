import type { Metadata } from "next";
import { Suspense } from "react";

import { AuthShell } from "@/components/auth/auth-shell";
import { SignInForm } from "@/components/auth/auth-forms";
import { Skeleton } from "@/components/ui/primitives";

export const metadata: Metadata = {
  title: "Sign in",
  description: "Sign in to Waypoint to pick your trips back up where you left them.",
};

export default function SignInPage() {
  return (
    <AuthShell
      title="Welcome back"
      subtitle="Pick your trips back up where you left them."
      image="parisBridge"
      highlights={[
        "Every itinerary you've built, kept and versioned",
        "Preferences remembered, so you never restate them",
        "Share a plan with a link your travel companions can open",
      ]}
    >
      {/* useSearchParams needs a Suspense boundary in the App Router. */}
      <Suspense fallback={<Skeleton className="h-96 w-full rounded-2xl" />}>
        <SignInForm />
      </Suspense>
    </AuthShell>
  );
}
