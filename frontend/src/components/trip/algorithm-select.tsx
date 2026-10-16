"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/controls";
import { Field } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import type { AlgorithmInfo, OptimizerAlgorithm } from "@/types/api";

/** What a plan uses when nobody has chosen: compare everything, keep the best. */
export const DEFAULT_ALGORITHM: OptimizerAlgorithm = "auto";

/**
 * The algorithm list, fetched once and shared by every selector on the page.
 *
 * It is static for a given deployment — labels, speeds and descriptions all come
 * from the server's own enum — so it never needs refetching within a session.
 */
export function useAlgorithms() {
  return useQuery({
    queryKey: ["optimizer-algorithms"],
    queryFn: () => api.itineraries.algorithms(),
    staleTime: Infinity,
    gcTime: Infinity,
  });
}

/**
 * Choose which algorithm plans a route.
 *
 * The options, their labels and their descriptions are served by
 * `GET /itineraries/optimizer/algorithms`, so this cannot drift out of step with
 * the algorithms the backend actually offers — a new one appears here as soon as
 * the server knows about it, and one that is removed stops being offered.
 */
export function AlgorithmSelect({
  value,
  onChange,
  disabled,
  label = "Route algorithm",
  className,
  id = "optimizer-algorithm",
}: {
  /** `null` means the plan has no saved choice and defers to the server default. */
  value: OptimizerAlgorithm | null;
  onChange: (algorithm: OptimizerAlgorithm) => void;
  disabled?: boolean;
  label?: string;
  className?: string;
  id?: string;
}) {
  const { data, isPending, isError } = useAlgorithms();

  const selected = value ?? DEFAULT_ALGORITHM;
  const current = useMemo<AlgorithmInfo | undefined>(
    () => data?.find((algorithm) => algorithm.key === selected),
    [data, selected],
  );

  // The hint carries the description and speed, so the trade-off is visible
  // before choosing rather than only after a slow plan comes back.
  const hint = isError
    ? "The algorithm list could not be loaded, so the server default will be used."
    : current
      ? `${current.description} Speed: ${current.speed}; typical quality: ${current.quality}.`
      : undefined;

  return (
    <Field label={label} htmlFor={id} hint={hint} className={className}>
      <Select
        value={selected}
        onValueChange={(next) => onChange(next as OptimizerAlgorithm)}
        disabled={disabled || isPending || isError}
      >
        <SelectTrigger id={id} aria-label={label}>
          <SelectValue placeholder={isPending ? "Loading…" : "Server default"} />
        </SelectTrigger>
        <SelectContent>
          {(data ?? []).map((algorithm) => (
            <SelectItem key={algorithm.key} value={algorithm.key}>
              {algorithm.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Field>
  );
}
