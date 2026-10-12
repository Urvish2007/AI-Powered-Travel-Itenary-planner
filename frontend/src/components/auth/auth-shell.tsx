import { Check } from "lucide-react";
import Image from "next/image";
import Link from "next/link";

import { Logo } from "@/components/layout/logo";
import { blurPhoto, photo } from "@/lib/images";
import type { ImageKey } from "@/lib/images";

/**
 * Split layout for the sign-in and sign-up pages.
 *
 * The photograph does real work here: an account page with nothing but a form
 * on white is where sign-up funnels leak. The bullet list beside it answers
 * "why am I creating an account?" at exactly the moment the question occurs.
 */
export function AuthShell({
  title,
  subtitle,
  image = "parisBridge",
  highlights,
  children,
  footer,
}: {
  title: string;
  subtitle: string;
  image?: ImageKey;
  highlights?: string[];
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <div className="grid min-h-svh lg:grid-cols-2">
      {/* Form side */}
      <div className="flex flex-col px-4 py-8 sm:px-8 lg:px-12">
        <header className="flex items-center justify-between">
          <Logo />
          <Link
            href="/"
            className="text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            Back to site
          </Link>
        </header>

        <main
          id="main"
          className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center py-10"
        >
          <div className="mb-8">
            <h1 className="text-3xl font-extrabold">{title}</h1>
            <p className="mt-2 text-muted-foreground">{subtitle}</p>
          </div>
          {children}
        </main>

        {footer && (
          <footer className="mx-auto w-full max-w-md pb-2 text-center text-sm text-muted-foreground">
            {footer}
          </footer>
        )}
      </div>

      {/* Photograph side — hidden on small screens, where it would push the
          form below the fold for no benefit. */}
      <div className="relative hidden lg:block">
        <Image
          src={photo(image, { width: 1200, height: 1600 })}
          alt=""
          fill
          sizes="50vw"
          placeholder="blur"
          blurDataURL={blurPhoto(image)}
          className="object-cover"
          priority
        />
        {/* Bottom-weighted only: the panel sits at the bottom, so darkening the
            top as well would flatten the photograph for no benefit. */}
        <div className="absolute inset-0 bg-gradient-to-t from-slate-950/85 via-slate-950/20 to-transparent" />

        {highlights && highlights.length > 0 && (
          <div className="absolute inset-x-0 bottom-0 p-10 xl:p-14">
            <div className="glass-dark max-w-md rounded-3xl p-7">
              <p className="text-sm font-semibold uppercase tracking-wider text-white/70">
                With a free account
              </p>
              <ul className="mt-4 space-y-3">
                {highlights.map((item) => (
                  <li key={item} className="flex items-start gap-3 text-white">
                    <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-primary">
                      <Check className="size-3" strokeWidth={3} />
                    </span>
                    <span className="text-sm leading-relaxed">{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
