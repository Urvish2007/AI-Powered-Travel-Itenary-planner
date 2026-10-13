import Image from "next/image";

import { blurPhoto, cityGradient, cityImage, hasCityPhoto, photo } from "@/lib/images";

/**
 * The picture behind a city card: its photograph when we have one, otherwise a
 * plain gradient with the city's initial. Never an unrelated stock photo.
 */
export function CityBackdrop({
  city,
  country,
  priority = false,
  sizes,
  className,
}: {
  city: string;
  country?: string;
  priority?: boolean;
  sizes: string;
  className?: string;
}) {
  if (hasCityPhoto(city)) {
    const key = cityImage(city);
    return (
      <Image
        src={photo(key, { width: 700, height: 900 })}
        alt={country ? `${city}, ${country}` : city}
        fill
        priority={priority}
        sizes={sizes}
        placeholder="blur"
        blurDataURL={blurPhoto(key)}
        className={className}
      />
    );
  }

  return (
    <div
      aria-hidden="true"
      className="absolute inset-0 overflow-hidden"
      style={{ background: cityGradient(city) }}
    >
      <span className="absolute -right-2 -top-6 select-none text-[11rem] font-black leading-none text-white/10">
        {city.trim().charAt(0).toUpperCase()}
      </span>
    </div>
  );
}
