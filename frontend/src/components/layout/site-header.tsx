"use client";

import {
  Compass,
  LayoutDashboard,
  LogOut,
  Map,
  Menu,
  Settings,
  Sparkles,
  User as UserIcon,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Logo } from "@/components/layout/logo";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import { Button } from "@/components/ui/button";
import { Avatar } from "@/components/ui/controls";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/overlays";
import { Skeleton } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/store/auth";

const NAV_LINKS = [
  { href: "/destinations", label: "Destinations", icon: Compass },
  { href: "/plan", label: "Plan a trip", icon: Sparkles },
  { href: "/trips", label: "My trips", icon: Map, authOnly: true },
];

export function SiteHeader({
  /**
   * Float over a hero image rather than sitting on a solid bar. The header
   * swaps to its solid treatment as soon as the page scrolls, so the links stay
   * legible once the photo has passed.
   */
  transparent = false,
}: {
  transparent?: boolean;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, isAuthenticated, isLoading, signOut } = useAuthStore();
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 16);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // A route change should never leave the mobile menu hanging open. Adjusting
  // during render rather than in an effect means the menu is already closed on
  // the first frame of the new page, with no flash of the open overlay.
  const [menuPathname, setMenuPathname] = useState(pathname);
  if (menuPathname !== pathname) {
    setMenuPathname(pathname);
    setMenuOpen(false);
  }

  // The open menu covers the page, so the page behind it must not scroll.
  useEffect(() => {
    document.body.style.overflow = menuOpen ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [menuOpen]);

  const solid = !transparent || scrolled || menuOpen;
  const visibleLinks = NAV_LINKS.filter((l) => !l.authOnly || isAuthenticated);

  async function handleSignOut() {
    await signOut();
    router.push("/");
  }

  return (
    <header
      className={cn(
        "fixed inset-x-0 top-0 z-50 transition-all duration-300",
        solid
          ? "border-b border-border bg-background/85 backdrop-blur-xl"
          : "border-b border-transparent",
      )}
    >
      <div className="mx-auto flex h-16 max-w-7xl items-center gap-3 px-4 sm:h-18 sm:px-6 lg:px-8">
        <Logo className={cn(!solid && "text-white")} />

        <nav
          className="ml-6 hidden items-center gap-1 md:flex"
          aria-label="Main"
        >
          {visibleLinks.map((link) => {
            const active =
              pathname === link.href || pathname.startsWith(`${link.href}/`);
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "rounded-full px-4 py-2 text-sm font-medium transition-colors",
                  solid
                    ? active
                      ? "bg-secondary text-foreground"
                      : "text-muted-foreground hover:bg-secondary hover:text-foreground"
                    : active
                      ? "bg-white/20 text-white"
                      : "text-white/85 hover:bg-white/15 hover:text-white",
                )}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
          <ThemeToggle className={cn(!solid && "text-white/90 hover:bg-white/15 hover:text-white")} />

          {isLoading ? (
            <Skeleton className="size-10 rounded-full" />
          ) : isAuthenticated && user ? (
            <DropdownMenu>
              <DropdownMenuTrigger
                className="rounded-full focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                aria-label="Account menu"
              >
                <Avatar src={user.avatar_url} name={user.full_name} size={38} />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-60">
                <DropdownMenuLabel className="normal-case tracking-normal">
                  <span className="block truncate text-sm font-semibold text-foreground">
                    {user.full_name}
                  </span>
                  <span className="block truncate text-xs font-normal text-muted-foreground">
                    {user.email}
                  </span>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild>
                  <Link href="/trips">
                    <LayoutDashboard />
                    My trips
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/account">
                    <UserIcon />
                    Profile
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/account#preferences">
                    <Settings />
                    Travel preferences
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem destructive onClick={handleSignOut}>
                  <LogOut />
                  Sign out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <div className="hidden items-center gap-2 sm:flex">
              <Button
                asChild
                variant={solid ? "ghost" : "glass"}
                size="sm"
                className={cn(!solid && "text-white")}
              >
                <Link href="/sign-in">Sign in</Link>
              </Button>
              <Button asChild size="sm">
                <Link href="/sign-up">Get started</Link>
              </Button>
            </div>
          )}

          <button
            type="button"
            onClick={() => setMenuOpen((open) => !open)}
            className={cn(
              "inline-flex size-10 items-center justify-center rounded-full transition-colors md:hidden",
              solid
                ? "text-foreground hover:bg-secondary"
                : "text-white hover:bg-white/15",
            )}
            aria-label={menuOpen ? "Close menu" : "Open menu"}
            aria-expanded={menuOpen}
          >
            {menuOpen ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>
        </div>
      </div>

      {/* Mobile navigation */}
      {menuOpen && (
        <div className="fixed inset-x-0 top-16 z-50 h-[calc(100dvh-4rem)] overflow-y-auto border-t border-border bg-background px-4 py-6 md:hidden">
          <nav className="flex flex-col gap-1" aria-label="Mobile">
            {visibleLinks.map((link) => {
              const Icon = link.icon;
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className="flex items-center gap-3 rounded-xl px-4 py-3.5 text-base font-medium hover:bg-secondary"
                >
                  <Icon className="size-5 text-muted-foreground" />
                  {link.label}
                </Link>
              );
            })}
          </nav>

          {!isAuthenticated && !isLoading && (
            <div className="mt-6 flex flex-col gap-3 border-t border-border pt-6">
              <Button asChild variant="outline" size="lg" full>
                <Link href="/sign-in">Sign in</Link>
              </Button>
              <Button asChild size="lg" full>
                <Link href="/sign-up">Create a free account</Link>
              </Button>
            </div>
          )}

          {isAuthenticated && (
            <div className="mt-6 flex flex-col gap-1 border-t border-border pt-6">
              <Link
                href="/account"
                className="flex items-center gap-3 rounded-xl px-4 py-3.5 text-base font-medium hover:bg-secondary"
              >
                <UserIcon className="size-5 text-muted-foreground" />
                Profile
              </Link>
              <button
                type="button"
                onClick={handleSignOut}
                className="flex items-center gap-3 rounded-xl px-4 py-3.5 text-left text-base font-medium text-destructive hover:bg-destructive/10"
              >
                <LogOut className="size-5" />
                Sign out
              </button>
            </div>
          )}
        </div>
      )}
    </header>
  );
}
