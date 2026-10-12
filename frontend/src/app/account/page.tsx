import type { Metadata } from "next";

import { AccountView } from "@/components/account/account-view";
import { RequireAuth } from "@/components/auth/require-auth";
import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";

export const metadata: Metadata = {
  title: "Your account",
  description: "Profile, travel preferences and sign-in methods.",
  robots: { index: false, follow: false },
};

export default function AccountPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="min-h-[70svh] pt-16 sm:pt-18">
        <RequireAuth>
          <AccountView />
        </RequireAuth>
      </main>
      <SiteFooter />
    </>
  );
}
