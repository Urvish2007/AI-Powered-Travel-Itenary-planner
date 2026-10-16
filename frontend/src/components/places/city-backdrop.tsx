import Image from "next/image";

import { blurPhoto, cityGradient, cityImage, hasCityPhoto, photo } from "@/lib/images";
import { cn } from "@/lib/utils";
import type { CityPhoto } from "@/types/api";

/**
 * The picture behind a city: its licensed photograph when we have one, then a
 * curated photo of that city, otherwise a plain gradient with its initial.
 * Never an unrelated stock photo.
 */
export function CityBackdrop({
  city,
  country,
  cityPhoto,
  priority = false,
  sizes,
  className,
  showCredit = true,
}: {
  city: string;
  country?: string;
  /** A licensed photo from the API; shown with its credit. */
  cityPhoto?: CityPhoto | null;
  priority?: boolean;
  sizes: string;
  className?: string;
  /**
   * Render the credit here. A caller that layers the image under other content
   * turns this off and places a linked <PhotoCredit> above that content.
   */
  showCredit?: boolean;
}) {
  const alt = country ? `${city}, ${country}` : city;

  if (cityPhoto?.image_url) {
    return (
      <>
        <Image
          src={cityPhoto.image_url}
          alt={alt}
          fill
          priority={priority}
          sizes={sizes}
          className={className}
        />
        {/* Unlinked here: a backdrop usually sits inside a link or button,
            where a nested link would be invalid. */}
        {showCredit && <PhotoCredit photo={cityPhoto} linked={false} />}
      </>
    );
  }

  if (hasCityPhoto(city)) {
    const key = cityImage(city);
    return (
      <Image
        src={photo(key, { width: 700, height: 900 })}
        alt={alt}
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

/**
 * What a Creative Commons licence asks us to show: who made the photo, under
 * which licence, and where it came from.
 */
export function PhotoCredit({
  photo: credit,
  linked,
  className,
}: {
  photo: CityPhoto;
  linked: boolean;
  className?: string;
}) {
  const author = credit.author || "Wikimedia Commons contributor";
  const licence = credit.license || "see source";

  return (
    <p
      className={cn(
        "absolute right-2 top-2 z-10 max-w-[70%] truncate rounded bg-black/45 px-1.5 py-0.5 text-[10px] leading-tight text-white/85",
        className,
      )}
      title={`Photo: ${author} · ${licence} · via Wikimedia Commons`}
    >
      {linked && credit.source_url ? (
        <>
          Photo:{" "}
          <a href={credit.source_url} target="_blank" rel="noopener noreferrer" className="underline">
            {author}
          </a>
          {" · "}
          {credit.license_url ? (
            <a
              href={credit.license_url}
              target="_blank"
              rel="noopener noreferrer license"
              className="underline"
            >
              {licence}
            </a>
          ) : (
            licence
          )}
        </>
      ) : (
        `Photo: ${author} · ${licence}`
      )}
    </p>
  );
}
