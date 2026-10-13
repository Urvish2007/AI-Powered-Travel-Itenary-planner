"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, LogOut, ShieldCheck, Trash2, Unlink } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Avatar, Slider } from "@/components/ui/controls";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/overlays";
import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Field,
  Input,
  Separator,
} from "@/components/ui/primitives";
import { ApiError, api } from "@/lib/api";
import { cn, titleCase } from "@/lib/utils";
import { useAuthStore } from "@/store/auth";
import type {
  AccessibilityNeed,
  BudgetLevel,
  InterestTag,
  TransportMode,
  TravelPace,
} from "@/types/api";

const INTERESTS: InterestTag[] = [
  "history", "art", "food", "nature", "adventure", "nightlife", "shopping",
  "relaxation", "family", "photography", "architecture", "culture", "sports", "music",
];
const PACES: TravelPace[] = ["relaxed", "moderate", "packed"];
const BUDGETS: BudgetLevel[] = ["shoestring", "budget", "moderate", "comfortable", "luxury"];
const TRANSPORT: TransportMode[] = ["walking", "transit", "cycling", "driving"];
const ACCESS: AccessibilityNeed[] = ["wheelchair", "step_free", "low_walking", "hearing", "visual"];

const PROVIDER_LABEL: Record<string, string> = {
  google: "Google",
  apple: "Apple",
  password: "Email and password",
};

export function AccountView() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { user, setUser, signOut } = useAuthStore();

  const [fullName, setFullName] = useState("");
  const [homeCity, setHomeCity] = useState("");
  const [prefs, setPrefs] = useState(user?.preferences ?? null);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  // Seed the form once the user arrives, and again if they change elsewhere.
  // Adjusting during render rather than in an effect means the inputs are
  // already populated on the first paint, instead of flashing empty.
  const [seededFrom, setSeededFrom] = useState<typeof user>(null);
  if (user && seededFrom !== user) {
    setSeededFrom(user);
    setFullName(user.full_name);
    setHomeCity(user.home_city ?? "");
    setPrefs(user.preferences);
  }

  const linkedQuery = useQuery({
    queryKey: ["linked-providers"],
    queryFn: () => api.auth.linked(),
  });

  const saveProfile = useMutation({
    mutationFn: () =>
      api.users.update({ full_name: fullName.trim(), home_city: homeCity.trim() }),
    onSuccess(updated) {
      setUser(updated);
      toast.success("Profile saved.");
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : "Could not save."),
  });

  const savePrefs = useMutation({
    mutationFn: () => api.users.updatePreferences(prefs!),
    onSuccess(updated) {
      setUser(updated);
      toast.success("Preferences saved. New trips will start from these.");
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : "Could not save."),
  });

  const unlink = useMutation({
    mutationFn: (provider: "google" | "apple") => api.auth.unlink(provider),
    onSuccess() {
      queryClient.invalidateQueries({ queryKey: ["linked-providers"] });
      toast.success("Sign-in method removed.");
    },
    onError: (error) =>
      toast.error(
        error instanceof ApiError ? error.message : "Could not unlink that.",
      ),
  });

  const deactivate = useMutation({
    mutationFn: () => api.users.deactivate(),
    async onSuccess() {
      toast.success("Your account has been deactivated.");
      await signOut();
      router.push("/");
    },
  });

  if (!user || !prefs) return null;

  const patchPrefs = (changes: Partial<typeof prefs>) =>
    setPrefs((current) => (current ? { ...current, ...changes } : current));

  const toggle = <T,>(list: T[], value: T): T[] =>
    list.includes(value) ? list.filter((item) => item !== value) : [...list, value];

  return (
    <div className="mx-auto max-w-3xl space-y-8 px-4 py-10 sm:px-6 lg:py-14">
      <header className="flex items-center gap-4">
        <Avatar src={user.avatar_url} name={user.full_name} size={64} />
        <div className="min-w-0">
          <h1 className="truncate text-2xl font-extrabold sm:text-3xl">
            {user.full_name}
          </h1>
          <p className="truncate text-muted-foreground">{user.email}</p>
          {user.is_verified && (
            <Badge variant="success" size="sm" className="mt-1.5">
              <ShieldCheck />
              Verified
            </Badge>
          )}
        </div>
      </header>

      {/* Profile */}
      <Card>
        <CardHeader>
          <CardTitle>Profile</CardTitle>
          <CardDescription>How you appear across the app.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field label="Name" htmlFor="full-name">
            <Input
              id="full-name"
              value={fullName}
              onChange={(event) => setFullName(event.target.value)}
              autoComplete="name"
            />
          </Field>
          <Field
            label="Home city"
            htmlFor="home-city"
            hint="Used to work out how far you are travelling."
          >
            <Input
              id="home-city"
              value={homeCity}
              onChange={(event) => setHomeCity(event.target.value)}
              placeholder="London"
              autoComplete="address-level2"
            />
          </Field>
          <Button
            onClick={() => saveProfile.mutate()}
            isLoading={saveProfile.isPending}
            loadingText="Saving…"
            disabled={!fullName.trim()}
          >
            Save profile
          </Button>
        </CardContent>
      </Card>

      {/* Travel preferences */}
      <Card id="preferences">
        <CardHeader>
          <CardTitle>Travel preferences</CardTitle>
          <CardDescription>
            Every new trip starts from these, so you never have to restate them.
            Changing them does not alter trips you have already planned.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-7">
          <fieldset>
            <legend className="text-sm font-medium">Interests</legend>
            <div className="mt-3 flex flex-wrap gap-2">
              {INTERESTS.map((interest) => {
                const active = prefs.interests.includes(interest);
                return (
                  <button
                    key={interest}
                    type="button"
                    onClick={() =>
                      patchPrefs({ interests: toggle(prefs.interests, interest) })
                    }
                    aria-pressed={active}
                    className={cn(
                      "rounded-full border px-3.5 py-2 text-sm font-medium transition-all",
                      active
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-border hover:border-foreground/25",
                    )}
                  >
                    {titleCase(interest)}
                  </button>
                );
              })}
            </div>
          </fieldset>

          <ChipRow
            legend="Pace"
            options={PACES}
            value={prefs.pace}
            onChange={(pace) => patchPrefs({ pace })}
          />
          <ChipRow
            legend="Budget"
            options={BUDGETS}
            value={prefs.budget_level}
            onChange={(budget_level) => patchPrefs({ budget_level })}
          />
          <ChipRow
            legend="Getting around"
            options={TRANSPORT}
            value={prefs.preferred_transport}
            onChange={(preferred_transport) => patchPrefs({ preferred_transport })}
          />

          <div>
            <div className="flex items-baseline justify-between">
              <label htmlFor="walk-pref" className="text-sm font-medium">
                Most you&rsquo;ll walk in a day
              </label>
              <span className="text-sm font-semibold text-primary tabular-nums">
                {prefs.max_daily_walking_km} km
              </span>
            </div>
            <Slider
              id="walk-pref"
              className="mt-3"
              min={1}
              max={25}
              step={1}
              value={[prefs.max_daily_walking_km]}
              onValueChange={([value]) => patchPrefs({ max_daily_walking_km: value })}
              aria-label="Maximum walking per day in kilometres"
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Days start at" htmlFor="pref-start">
              <Input
                id="pref-start"
                type="time"
                value={prefs.day_start_time}
                onChange={(event) =>
                  patchPrefs({ day_start_time: event.target.value })
                }
              />
            </Field>
            <Field label="Days end by" htmlFor="pref-end">
              <Input
                id="pref-end"
                type="time"
                value={prefs.day_end_time}
                onChange={(event) => patchPrefs({ day_end_time: event.target.value })}
              />
            </Field>
          </div>

          <fieldset>
            <legend className="text-sm font-medium">Access needs</legend>
            <div className="mt-3 flex flex-wrap gap-2">
              {ACCESS.map((need) => {
                const active = prefs.accessibility_needs.includes(need);
                return (
                  <button
                    key={need}
                    type="button"
                    onClick={() =>
                      patchPrefs({
                        accessibility_needs: toggle(prefs.accessibility_needs, need),
                      })
                    }
                    aria-pressed={active}
                    className={cn(
                      "rounded-full border px-3.5 py-2 text-sm font-medium transition-all",
                      active
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-border hover:border-foreground/25",
                    )}
                  >
                    {titleCase(need)}
                  </button>
                );
              })}
            </div>
          </fieldset>

          <Button
            onClick={() => savePrefs.mutate()}
            isLoading={savePrefs.isPending}
            loadingText="Saving…"
          >
            Save preferences
          </Button>
        </CardContent>
      </Card>

      {/* Sign-in methods */}
      <Card>
        <CardHeader>
          <CardTitle>Sign-in methods</CardTitle>
          <CardDescription>
            How you get into this account. At least one must always remain.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {linkedQuery.data?.providers.map((provider) => (
            <div
              key={provider}
              className="flex items-center justify-between gap-3 rounded-xl border border-border px-4 py-3"
            >
              <span className="font-medium">
                {PROVIDER_LABEL[provider] ?? titleCase(provider)}
              </span>
              {provider !== "password" && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => unlink.mutate(provider as "google" | "apple")}
                  isLoading={unlink.isPending}
                >
                  <Unlink />
                  Unlink
                </Button>
              )}
            </div>
          ))}

          {linkedQuery.data && !linkedQuery.data.has_password && (
            <p className="rounded-xl bg-accent-soft px-4 py-3 text-sm text-accent-foreground">
              You sign in with a social account only. Adding a password gives you
              a way in if you ever lose access to it.
            </p>
          )}

          <Separator className="my-2" />

          <Button variant="outline" onClick={() => setPasswordOpen(true)}>
            <KeyRound />
            {linkedQuery.data?.has_password ? "Change password" : "Add a password"}
          </Button>
        </CardContent>
      </Card>

      {/* Danger zone */}
      <Card className="border-destructive/30">
        <CardHeader>
          <CardTitle className="text-destructive">Account</CardTitle>
          <CardDescription>
            Signing out ends this session. Deactivating disables the account
            entirely.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-3">
          <Button
            variant="outline"
            onClick={async () => {
              await signOut();
              router.push("/");
            }}
          >
            <LogOut />
            Sign out
          </Button>
          <Button variant="destructive" onClick={() => setDeleteOpen(true)}>
            <Trash2 />
            Deactivate account
          </Button>
        </CardContent>
      </Card>

      <PasswordDialog
        open={passwordOpen}
        onOpenChange={setPasswordOpen}
        hasPassword={Boolean(linkedQuery.data?.has_password)}
        onDone={() => queryClient.invalidateQueries({ queryKey: ["linked-providers"] })}
      />

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent sheetOnMobile>
          <DialogHeader>
            <DialogTitle>Deactivate your account?</DialogTitle>
            <DialogDescription>
              You will be signed out everywhere and will not be able to sign back
              in. Your trips are kept, not deleted — contact support to restore
              access.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => deactivate.mutate()}
              isLoading={deactivate.isPending}
            >
              Deactivate
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function ChipRow<T extends string>({
  legend,
  options,
  value,
  onChange,
}: {
  legend: string;
  options: T[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <fieldset>
      <legend className="text-sm font-medium">{legend}</legend>
      <div className="mt-3 flex flex-wrap gap-2">
        {options.map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => onChange(option)}
            aria-pressed={value === option}
            className={cn(
              "rounded-full border px-3.5 py-2 text-sm font-medium transition-all",
              value === option
                ? "border-primary bg-primary text-primary-foreground"
                : "border-border hover:border-foreground/25",
            )}
          >
            {titleCase(option)}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

function PasswordDialog({
  open,
  onOpenChange,
  hasPassword,
  onDone,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  hasPassword: boolean;
  onDone: () => void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");

  const mutation = useMutation({
    mutationFn: () =>
      hasPassword
        ? api.auth.changePassword({ current_password: current, new_password: next })
        : api.auth.setPassword(next),
    onSuccess(result) {
      toast.success(result.message);
      setCurrent("");
      setNext("");
      onOpenChange(false);
      onDone();
    },
    onError: (error) =>
      toast.error(
        error instanceof ApiError ? error.message : "Could not update the password.",
      ),
  });

  const tooShort = next.length > 0 && next.length < 10;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent sheetOnMobile>
        <DialogHeader>
          <DialogTitle>
            {hasPassword ? "Change your password" : "Add a password"}
          </DialogTitle>
          <DialogDescription>
            {hasPassword
              ? "You will be signed out of every other device."
              : "This gives you a second way into your account."}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {hasPassword && (
            <Field label="Current password" htmlFor="current-password">
              <Input
                id="current-password"
                type="password"
                autoComplete="current-password"
                value={current}
                onChange={(event) => setCurrent(event.target.value)}
              />
            </Field>
          )}
          <Field
            label="New password"
            htmlFor="new-password"
            hint="At least 10 characters, with a letter and a number"
            error={tooShort ? "Use at least 10 characters" : undefined}
          >
            <Input
              id="new-password"
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(event) => setNext(event.target.value)}
              aria-invalid={tooShort}
            />
          </Field>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            isLoading={mutation.isPending}
            disabled={next.length < 10 || (hasPassword && current.length === 0)}
          >
            {hasPassword ? "Change password" : "Set password"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
