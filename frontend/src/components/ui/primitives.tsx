"use client";

/**
 * Small UI primitives, kept together because each is a handful of lines and
 * splitting them across fifteen files costs more in navigation than it saves.
 * Anything that grows an internal state machine graduates to its own module.
 */

import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------

export const Card = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement> & { interactive?: boolean }
>(({ className, interactive, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      "rounded-2xl border border-border bg-card text-card-foreground shadow-sm",
      interactive &&
        "transition-all duration-300 hover:-translate-y-1 hover:shadow-lg",
      className,
    )}
    {...props}
  />
));
Card.displayName = "Card";

export const CardHeader = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("flex flex-col gap-1.5 p-6", className)} {...props} />
));
CardHeader.displayName = "CardHeader";

export const CardTitle = React.forwardRef<
  HTMLHeadingElement,
  React.HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h3
    ref={ref}
    className={cn("text-lg font-semibold leading-tight", className)}
    {...props}
  />
));
CardTitle.displayName = "CardTitle";

export const CardDescription = React.forwardRef<
  HTMLParagraphElement,
  React.HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <p
    ref={ref}
    className={cn("text-sm text-muted-foreground", className)}
    {...props}
  />
));
CardDescription.displayName = "CardDescription";

export const CardContent = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("p-6 pt-0", className)} {...props} />
));
CardContent.displayName = "CardContent";

export const CardFooter = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("flex items-center gap-3 p-6 pt-0", className)}
    {...props}
  />
));
CardFooter.displayName = "CardFooter";

// ---------------------------------------------------------------------------
// Badge
// ---------------------------------------------------------------------------

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full font-medium transition-colors [&_svg]:size-3.5 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-secondary text-secondary-foreground",
        primary: "bg-primary-soft text-primary",
        accent: "bg-accent-soft text-accent-foreground",
        success: "bg-success/12 text-success",
        warning: "bg-warning/15 text-warning-foreground dark:text-warning",
        destructive: "bg-destructive/12 text-destructive",
        outline: "border border-border text-foreground",
        glass: "glass-dark text-white",
      },
      size: {
        sm: "px-2 py-0.5 text-[11px]",
        md: "px-2.5 py-1 text-xs",
        lg: "px-3 py-1.5 text-sm",
      },
    },
    defaultVariants: { variant: "default", size: "md" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, size, ...props }: BadgeProps) {
  return (
    <span className={cn(badgeVariants({ variant, size }), className)} {...props} />
  );
}

// ---------------------------------------------------------------------------
// Input / Textarea
// ---------------------------------------------------------------------------

export const Input = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(({ className, type = "text", ...props }, ref) => (
  <input
    ref={ref}
    type={type}
    className={cn(
      "flex h-11 w-full rounded-xl border border-input bg-background px-4 text-sm",
      "placeholder:text-muted-foreground",
      "transition-colors focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30",
      "disabled:cursor-not-allowed disabled:opacity-60",
      "aria-[invalid=true]:border-destructive aria-[invalid=true]:ring-destructive/25",
      // Hide the native date/time picker icon in dark mode, where it renders black.
      "dark:[color-scheme:dark]",
      className,
    )}
    {...props}
  />
));
Input.displayName = "Input";

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      "flex min-h-24 w-full rounded-xl border border-input bg-background px-4 py-3 text-sm",
      "placeholder:text-muted-foreground resize-y",
      "transition-colors focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30",
      "disabled:cursor-not-allowed disabled:opacity-60",
      "aria-[invalid=true]:border-destructive aria-[invalid=true]:ring-destructive/25",
      className,
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";

// ---------------------------------------------------------------------------
// Label + field wrapper
// ---------------------------------------------------------------------------

export const Label = React.forwardRef<
  HTMLLabelElement,
  React.LabelHTMLAttributes<HTMLLabelElement>
>(({ className, ...props }, ref) => (
  <label
    ref={ref}
    className={cn(
      "text-sm font-medium leading-none text-foreground",
      className,
    )}
    {...props}
  />
));
Label.displayName = "Label";

interface FieldProps {
  label?: string;
  htmlFor?: string;
  hint?: string;
  error?: string;
  required?: boolean;
  className?: string;
  children: React.ReactNode;
}

/**
 * Label + control + message, wired for accessibility.
 *
 * The error is rendered in a live region so a screen reader announces a
 * validation failure without the user having to hunt for it.
 */
export function Field({
  label,
  htmlFor,
  hint,
  error,
  required,
  className,
  children,
}: FieldProps) {
  return (
    <div className={cn("space-y-2", className)}>
      {label && (
        <Label htmlFor={htmlFor}>
          {label}
          {required && (
            <span className="ml-0.5 text-destructive" aria-hidden>
              *
            </span>
          )}
        </Label>
      )}
      {children}
      {error ? (
        <p className="text-xs font-medium text-destructive" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------

export function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("skeleton rounded-lg", className)}
      aria-hidden
      {...props}
    />
  );
}

// ---------------------------------------------------------------------------
// Separator
// ---------------------------------------------------------------------------

export function Separator({
  className,
  orientation = "horizontal",
  label,
}: {
  className?: string;
  orientation?: "horizontal" | "vertical";
  label?: string;
}) {
  if (label) {
    return (
      <div className={cn("flex items-center gap-4", className)}>
        <span className="h-px flex-1 bg-border" />
        <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
        <span className="h-px flex-1 bg-border" />
      </div>
    );
  }
  return (
    <div
      role="separator"
      aria-orientation={orientation}
      className={cn(
        orientation === "horizontal" ? "h-px w-full" : "h-full w-px",
        "bg-border",
        className,
      )}
    />
  );
}

// ---------------------------------------------------------------------------
// Rating
// ---------------------------------------------------------------------------

export function Rating({
  value,
  reviewCount,
  className,
  showValue = true,
}: {
  value: number;
  reviewCount?: number;
  className?: string;
  showValue?: boolean;
}) {
  const rounded = Math.round(value * 10) / 10;
  return (
    <span
      className={cn("inline-flex items-center gap-1 text-sm", className)}
      aria-label={`Rated ${rounded} out of 5${
        reviewCount ? ` from ${reviewCount} reviews` : ""
      }`}
    >
      <svg
        viewBox="0 0 20 20"
        className="size-4 fill-star"
        aria-hidden
        focusable="false"
      >
        <path d="M10 1.5l2.6 5.3 5.9.9-4.2 4.1 1 5.8L10 15l-5.3 2.6 1-5.8L1.5 7.7l5.9-.9z" />
      </svg>
      {showValue && <span className="font-semibold">{rounded.toFixed(1)}</span>}
      {reviewCount !== undefined && reviewCount > 0 && (
        <span className="text-muted-foreground">
          ({reviewCount >= 1000 ? `${Math.round(reviewCount / 1000)}k` : reviewCount})
        </span>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-4 rounded-2xl border border-dashed border-border px-6 py-16 text-center",
        className,
      )}
    >
      {icon && (
        <div className="flex size-14 items-center justify-center rounded-2xl bg-primary-soft text-primary [&_svg]:size-7">
          {icon}
        </div>
      )}
      <div className="space-y-1.5">
        <h3 className="text-lg font-semibold">{title}</h3>
        {description && (
          <p className="mx-auto max-w-md text-sm text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {action}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section heading
// ---------------------------------------------------------------------------

export function SectionHeading({
  eyebrow,
  title,
  description,
  align = "left",
  className,
  action,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  align?: "left" | "center";
  className?: string;
  action?: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between",
        align === "center" && "sm:flex-col sm:items-center",
        className,
      )}
    >
      <div className={cn("space-y-2", align === "center" && "text-center")}>
        {eyebrow && (
          <p className="text-sm font-semibold uppercase tracking-wider text-primary">
            {eyebrow}
          </p>
        )}
        <h2 className="text-2xl font-bold sm:text-3xl lg:text-4xl">{title}</h2>
        {description && (
          <p
            className={cn(
              "max-w-2xl text-base text-muted-foreground",
              align === "center" && "mx-auto",
            )}
          >
            {description}
          </p>
        )}
      </div>
      {action}
    </div>
  );
}
