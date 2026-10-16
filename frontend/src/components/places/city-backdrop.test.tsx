import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CityPhoto } from "@/types/api";

// next/image needs the Next runtime; a plain <img> keeps these tests about
// which picture is chosen and what is credited.
vi.mock("next/image", () => ({
  default: ({ src, alt }: { src: string; alt: string }) => (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={src} alt={alt} />
  ),
}));

const { CityBackdrop, PhotoCredit } = await import("@/components/places/city-backdrop");

const licensed: CityPhoto = {
  image_url: "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/K.jpg/1280px-K.jpg",
  width: 1280,
  height: 853,
  author: "Jane Doe",
  license: "CC BY-SA 4.0",
  license_url: "https://creativecommons.org/licenses/by-sa/4.0",
  source_url: "https://commons.wikimedia.org/wiki/File:K.jpg",
  source: "wikimedia",
};

describe("CityBackdrop", () => {
  it("shows a licensed photo with its author and licence", () => {
    render(<CityBackdrop city="Kyoto" country="Japan" cityPhoto={licensed} sizes="100vw" />);

    expect(screen.getByRole("img", { name: "Kyoto, Japan" })).toHaveAttribute(
      "src",
      licensed.image_url,
    );
    expect(screen.getByText("Photo: Jane Doe · CC BY-SA 4.0")).toBeInTheDocument();
    // Inside a card the credit is text: a link nested in a link is invalid.
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("falls back to the curated photo for a city that has one", () => {
    render(<CityBackdrop city="Paris" cityPhoto={null} sizes="100vw" />);
    expect(screen.getByRole("img", { name: "Paris" }).getAttribute("src")).toContain(
      "images.unsplash.com",
    );
    expect(screen.queryByText(/^Photo:/)).not.toBeInTheDocument();
  });

  it("falls back to a gradient, never an unrelated photo", () => {
    const { container } = render(<CityBackdrop city="Kyoto" sizes="100vw" />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(container.textContent).toBe("K");
  });

  it("can leave the credit to its caller", () => {
    render(<CityBackdrop city="Kyoto" cityPhoto={licensed} sizes="100vw" showCredit={false} />);
    expect(screen.queryByText(/^Photo:/)).not.toBeInTheDocument();
  });
});

describe("PhotoCredit", () => {
  it("links the author to the source and the licence to its text", () => {
    render(<PhotoCredit photo={licensed} linked />);
    expect(screen.getByRole("link", { name: "Jane Doe" })).toHaveAttribute(
      "href",
      licensed.source_url,
    );
    const licence = screen.getByRole("link", { name: "CC BY-SA 4.0" });
    expect(licence).toHaveAttribute("href", licensed.license_url);
    expect(licence).toHaveAttribute("rel", "noopener noreferrer license");
  });

  it("credits Commons when the author is unknown", () => {
    render(<PhotoCredit photo={{ ...licensed, author: null }} linked={false} />);
    expect(
      screen.getByText("Photo: Wikimedia Commons contributor · CC BY-SA 4.0"),
    ).toBeInTheDocument();
  });
});
