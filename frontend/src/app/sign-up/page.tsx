import type { Metadata } from "next";
import { Suspense } from "react";

import { AuthShell } from "@/components/auth/auth-shell";
import { SignUpForm } from "@/components/auth/auth-forms";
import { Skeleton } from "@/components/ui/primitives";

export const metadata: Metadata = {
  title: "Create your account",
  description:
    "Create a free Waypoint account and build your first optimised itinerary in minutes.",
};

export default function SignUpPage() {
  return (
    <AuthShell
      title="Start planning"
      subtitle="Free to use. No card, no trial countdown."
      image="fujiPagoda"
      highlights={[
        "Itineraries scheduled around real opening hours",
        "Route optimised so you walk less and see more",
        "Change any day and the times recalculate themselves",
      ]}
    >
      <Suspense fallback={<Skeleton className="h-96 w-full rounded-2xl" />}>
        <SignUpForm />
      </Suspense>
    </AuthShell>
  );
}
