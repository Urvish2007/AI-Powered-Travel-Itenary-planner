"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Eye, EyeOff } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { SocialSignIn } from "@/components/auth/social-buttons";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/primitives";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/store/auth";

/**
 * Only redirect to a path on this site.
 *
 * `?next=` is attacker-controllable, so an absolute URL here would turn the
 * sign-in page into an open redirect — a convincing phishing hop that starts on
 * a domain the user trusts.
 */
function safeRedirect(target: string | null): string {
  if (!target) return "/trips";
  if (!target.startsWith("/") || target.startsWith("//")) return "/trips";
  return target;
}

function PasswordInput({
  id,
  autoComplete,
  placeholder,
  invalid,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative">
      <Input
        id={id}
        type={visible ? "text" : "password"}
        autoComplete={autoComplete}
        placeholder={placeholder}
        aria-invalid={invalid}
        className="pr-12"
        {...props}
      />
      <button
        type="button"
        onClick={() => setVisible((v) => !v)}
        className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-lg p-2 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
        // The control toggles visibility of the field, so it must say which
        // state it will move to, not just "show password".
        aria-label={visible ? "Hide password" : "Show password"}
      >
        {visible ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
      </button>
    </div>
  );
}

/** Surface the server's message, which is written to be shown to a user. */
function reportError(error: unknown, fallback: string) {
  if (error instanceof ApiError) {
    toast.error(error.message);
    return;
  }
  toast.error(fallback);
}

// ---------------------------------------------------------------------------
// Sign in
// ---------------------------------------------------------------------------

const signInSchema = z.object({
  email: z.string().min(1, "Enter your email address").email("That does not look like an email address"),
  password: z.string().min(1, "Enter your password"),
});

type SignInValues = z.infer<typeof signInSchema>;

export function SignInForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const signIn = useAuthStore((s) => s.signIn);
  const next = safeRedirect(searchParams.get("next"));

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<SignInValues>({ resolver: zodResolver(signInSchema) });

  async function onSubmit(values: SignInValues) {
    try {
      await signIn(values.email, values.password);
      toast.success("Welcome back.");
      router.push(next);
      router.refresh();
    } catch (error) {
      reportError(error, "Could not sign you in. Please try again.");
    }
  }

  return (
    <div className="space-y-6">
      <SocialSignIn mode="signin" onSuccess={() => router.push(next)} />

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <Field label="Email" htmlFor="email" error={errors.email?.message}>
          <Input
            id="email"
            type="email"
            autoComplete="email"
            placeholder="you@example.com"
            aria-invalid={Boolean(errors.email)}
            {...register("email")}
          />
        </Field>

        <Field
          label="Password"
          htmlFor="password"
          error={errors.password?.message}
        >
          <PasswordInput
            id="password"
            autoComplete="current-password"
            placeholder="Your password"
            invalid={Boolean(errors.password)}
            {...register("password")}
          />
        </Field>

        <Button
          type="submit"
          size="lg"
          full
          isLoading={isSubmitting}
          loadingText="Signing in…"
        >
          Sign in
        </Button>
      </form>

      <p className="text-center text-sm text-muted-foreground">
        New here?{" "}
        <Link
          href={`/sign-up${next !== "/trips" ? `?next=${encodeURIComponent(next)}` : ""}`}
          className="font-semibold text-primary hover:underline"
        >
          Create a free account
        </Link>
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sign up
// ---------------------------------------------------------------------------

const signUpSchema = z.object({
  full_name: z.string().trim().min(2, "Tell us what to call you").max(200),
  email: z.string().min(1, "Enter your email address").email("That does not look like an email address"),
  // Mirrors the server's policy exactly, so the form never accepts something
  // the API will then reject.
  password: z
    .string()
    .min(10, "Use at least 10 characters")
    .max(128)
    .refine((v) => /[A-Za-z]/.test(v), "Include at least one letter")
    .refine((v) => /\d/.test(v), "Include at least one number"),
});

type SignUpValues = z.infer<typeof signUpSchema>;

/** Rough strength signal. Deliberately about length first, which is what matters. */
function passwordStrength(value: string): { score: number; label: string } {
  if (!value) return { score: 0, label: "" };
  let score = 0;
  if (value.length >= 10) score += 1;
  if (value.length >= 14) score += 1;
  if (/[A-Z]/.test(value) && /[a-z]/.test(value)) score += 1;
  if (/\d/.test(value) && /[^A-Za-z0-9]/.test(value)) score += 1;
  const labels = ["Too short", "Weak", "Good", "Strong", "Very strong"];
  return { score, label: labels[score] };
}

export function SignUpForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const signUp = useAuthStore((s) => s.signUp);
  const next = safeRedirect(searchParams.get("next"));

  const {
    register,
    handleSubmit,
    control,
    formState: { errors, isSubmitting },
  } = useForm<SignUpValues>({ resolver: zodResolver(signUpSchema) });

  // useWatch rather than watch(): watch() hands back a new function on every
  // render, which the React Compiler cannot memoize without risking stale UI.
  const password = useWatch({ control, name: "password" }) ?? "";
  const strength = passwordStrength(password);

  async function onSubmit(values: SignUpValues) {
    try {
      await signUp(values);
      toast.success("Account created. Let's plan something.");
      router.push(next === "/trips" ? "/plan" : next);
      router.refresh();
    } catch (error) {
      reportError(error, "Could not create your account. Please try again.");
    }
  }

  return (
    <div className="space-y-6">
      <SocialSignIn mode="signup" onSuccess={() => router.push("/plan")} />

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <Field label="Your name" htmlFor="full_name" error={errors.full_name?.message}>
          <Input
            id="full_name"
            autoComplete="name"
            placeholder="Alex Morgan"
            aria-invalid={Boolean(errors.full_name)}
            {...register("full_name")}
          />
        </Field>

        <Field label="Email" htmlFor="email" error={errors.email?.message}>
          <Input
            id="email"
            type="email"
            autoComplete="email"
            placeholder="you@example.com"
            aria-invalid={Boolean(errors.email)}
            {...register("email")}
          />
        </Field>

        <Field
          label="Password"
          htmlFor="password"
          error={errors.password?.message}
          hint={!password ? "At least 10 characters, with a letter and a number" : undefined}
        >
          <PasswordInput
            id="password"
            autoComplete="new-password"
            placeholder="Choose a password"
            invalid={Boolean(errors.password)}
            {...register("password")}
          />
          {password && !errors.password && (
            <div className="flex items-center gap-2 pt-1">
              <div className="flex flex-1 gap-1">
                {[0, 1, 2, 3].map((index) => (
                  <span
                    key={index}
                    className={cn(
                      "h-1 flex-1 rounded-full transition-colors",
                      index < strength.score
                        ? strength.score <= 1
                          ? "bg-destructive"
                          : strength.score === 2
                            ? "bg-warning"
                            : "bg-success"
                        : "bg-secondary",
                    )}
                  />
                ))}
              </div>
              <span className="text-xs text-muted-foreground">{strength.label}</span>
            </div>
          )}
        </Field>

        <Button
          type="submit"
          size="lg"
          full
          isLoading={isSubmitting}
          loadingText="Creating your account…"
        >
          Create account
        </Button>

        <p className="text-center text-xs leading-relaxed text-muted-foreground">
          By continuing you agree to our{" "}
          <Link href="/terms" className="underline underline-offset-2 hover:text-foreground">
            Terms
          </Link>{" "}
          and{" "}
          <Link href="/privacy" className="underline underline-offset-2 hover:text-foreground">
            Privacy Policy
          </Link>
          .
        </p>
      </form>

      <p className="text-center text-sm text-muted-foreground">
        Already have an account?{" "}
        <Link
          href={`/sign-in${next !== "/trips" ? `?next=${encodeURIComponent(next)}` : ""}`}
          className="font-semibold text-primary hover:underline"
        >
          Sign in
        </Link>
      </p>
    </div>
  );
}
