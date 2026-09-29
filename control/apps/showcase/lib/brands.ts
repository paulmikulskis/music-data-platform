import { marks } from "@mdp/contracts/marks";
import {
  siApplemusic,
  siBandcamp,
  siBillboard,
  siDeezer,
  siMusicbrainz,
  siShazam,
  siSoundcloud,
  siSpotify,
  siWikipedia,
  siYoutube,
} from "simple-icons";
import type { CSSProperties } from "react";
import type { sourceBrand } from "@mdp/contracts/source-wording";
import type { z } from "zod";
export type Brand = z.infer<typeof sourceBrand>;
type Icon = { title: string; path: string; hex: string };
// Glyphs come from the CC0 simple-icons package and ship inside the app; nothing is hot-linked.
const icons: Record<Brand, Icon | null> = {
  spotify: siSpotify,
  apple_music: siApplemusic,
  shazam: siShazam,
  billboard: siBillboard,
  musicbrainz: siMusicbrainz,
  deezer: siDeezer,
  wikipedia: siWikipedia,
  listenbrainz: null,
  youtube: siYoutube,
  bandcamp: siBandcamp,
  soundcloud: siSoundcloud,
};
export const brandName = (brand: Brand) => marks[brand].label;
export function brandIcon(brand: Brand) {
  return icons[brand];
}
// Dark marks use the foreground color for contrast.
export function brandTint(brand: Brand) {
  const hex = icons[brand]?.hex ?? "F0EDE6";
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b < 60 ? "#edf4f6" : `#${hex}`;
}
export function brandStyle(
  brand: Brand,
): CSSProperties & { "--brand": string } {
  return { "--brand": brandTint(brand) };
}
// A song copy's platform, as int_song_key__daily names it, to its brand.
const platforms: Record<string, Brand> = {
  spotify: "spotify",
  apple: "apple_music",
  apple_music: "apple_music",
  deezer: "deezer",
  soundcloud: "soundcloud",
  youtube: "youtube",
  bandcamp: "bandcamp",
  musicbrainz: "musicbrainz",
};
export function platformBrand(platform: string): Brand | null {
  return platforms[platform] ?? null;
}
// Public pages open in a new tab. Only ids a platform documents as public page ids get a link.
export function songLink(platform: string, id: string) {
  if (platform === "spotify" && /^[A-Za-z0-9]{22}$/.test(id))
    return {
      label: "Open on Spotify",
      href: `https://open.spotify.com/track/${id}`,
    };
  if (platform === "apple" && /^\d+$/.test(id))
    return {
      label: "Open on Apple Music",
      href: `https://music.apple.com/song/${id}`,
    };
  return null;
}
export function artistLink(platform: string, id: string) {
  if (platform === "spotify" && /^[A-Za-z0-9]{22}$/.test(id))
    return {
      label: "Open artist",
      href: `https://open.spotify.com/artist/${id}`,
    };
  if (platform === "apple" && /^\d+$/.test(id))
    return {
      label: "Open artist",
      href: `https://music.apple.com/artist/${id}`,
    };
  return null;
}
