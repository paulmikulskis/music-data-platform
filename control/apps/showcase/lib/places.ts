import type { Place } from "../components/number";
import { countryName } from "./music-facts";
// Coordinates for the Shazam charts in inputs/chart_seed.csv and for common markets.
// A place missing here is named in the text but gets no dot.
const cities: Record<string, [string, number, number]> = {
  "new-york-city": ["New York", 40.71, -74.01],
  "los-angeles": ["Los Angeles", 34.05, -118.24],
  chicago: ["Chicago", 41.88, -87.63],
  dallas: ["Dallas", 32.78, -96.8],
  houston: ["Houston", 29.76, -95.37],
  atlanta: ["Atlanta", 33.75, -84.39],
  philadelphia: ["Philadelphia", 39.95, -75.17],
  miami: ["Miami", 25.76, -80.19],
  phoenix: ["Phoenix", 33.45, -112.07],
  london: ["London", 51.51, -0.13],
  birmingham: ["Birmingham", 52.49, -1.89],
  manchester: ["Manchester", 53.48, -2.24],
  glasgow: ["Glasgow", 55.86, -4.25],
  leeds: ["Leeds", 53.8, -1.55],
  toronto: ["Toronto", 43.65, -79.38],
  montréal: ["Montréal", 45.5, -73.57],
  vancouver: ["Vancouver", 49.28, -123.12],
  calgary: ["Calgary", 51.05, -114.07],
  sydney: ["Sydney", -33.87, 151.21],
  melbourne: ["Melbourne", -37.81, 144.96],
  brisbane: ["Brisbane", -27.47, 153.03],
  perth: ["Perth", -31.95, 115.86],
  berlin: ["Berlin", 52.52, 13.4],
  hamburg: ["Hamburg", 53.55, 9.99],
  munich: ["Munich", 48.14, 11.58],
  köln: ["Köln", 50.94, 6.96],
  paris: ["Paris", 48.86, 2.35],
  lyon: ["Lyon", 45.76, 4.84],
  marseille: ["Marseille", 43.3, 5.37],
  toulouse: ["Toulouse", 43.6, 1.44],
  "são-paulo": ["São Paulo", -23.55, -46.63],
  "rio-de-janeiro": ["Rio de Janeiro", -22.91, -43.17],
  brasília: ["Brasília", -15.79, -47.88],
  "mexico-city": ["Mexico City", 19.43, -99.13],
  guadalajara: ["Guadalajara", 20.66, -103.35],
  monterrey: ["Monterrey", 25.69, -100.32],
  puebla: ["Puebla", 19.04, -98.21],
  tokyo: ["Tokyo", 35.68, 139.69],
  osaka: ["Osaka", 34.69, 135.5],
};
// Market centres by ISO code; Shazam's country slugs map onto the same codes.
const markets: Record<string, [string, number, number]> = {
  US: ["US", 39.8, -98.6],
  GB: ["UK", 54.0, -2.0],
  CA: ["Canada", 56.1, -106.3],
  AU: ["Australia", -25.3, 133.8],
  DE: ["Germany", 51.2, 10.4],
  FR: ["France", 46.2, 2.2],
  BR: ["Brazil", -14.2, -51.9],
  MX: ["Mexico", 23.6, -102.6],
  JP: ["Japan", 36.2, 138.3],
  ES: ["Spain", 40.5, -3.7],
  IT: ["Italy", 42.8, 12.6],
  NL: ["Netherlands", 52.1, 5.3],
  SE: ["Sweden", 60.1, 18.6],
  NO: ["Norway", 60.5, 8.5],
  DK: ["Denmark", 56.0, 10.0],
  FI: ["Finland", 61.9, 25.7],
  IE: ["Ireland", 53.1, -8.2],
  BE: ["Belgium", 50.5, 4.5],
  CH: ["Switzerland", 46.8, 8.2],
  AT: ["Austria", 47.5, 14.6],
  PL: ["Poland", 51.9, 19.1],
  PT: ["Portugal", 39.4, -8.2],
  TR: ["Turkey", 39.0, 35.2],
  IN: ["India", 20.6, 78.9],
  ID: ["Indonesia", -0.8, 113.9],
  PH: ["Philippines", 12.9, 121.8],
  KR: ["South Korea", 35.9, 127.8],
  TH: ["Thailand", 15.9, 101.0],
  VN: ["Vietnam", 14.1, 108.3],
  MY: ["Malaysia", 4.2, 102.0],
  SG: ["Singapore", 1.35, 103.8],
  NZ: ["New Zealand", -41.0, 174.0],
  AR: ["Argentina", -38.4, -63.6],
  CL: ["Chile", -35.7, -71.5],
  CO: ["Colombia", 4.6, -74.3],
  PE: ["Peru", -9.2, -75.0],
  ZA: ["South Africa", -30.6, 22.9],
  NG: ["Nigeria", 9.1, 8.7],
  EG: ["Egypt", 26.8, 30.8],
  SA: ["Saudi Arabia", 23.9, 45.1],
  AE: ["UAE", 23.4, 53.8],
  IL: ["Israel", 31.0, 34.9],
  UA: ["Ukraine", 48.4, 31.2],
  RO: ["Romania", 45.9, 25.0],
};
const slugs: Record<string, string> = {
  "united-states": "US",
  "united-kingdom": "GB",
  canada: "CA",
  australia: "AU",
  germany: "DE",
  france: "FR",
  brazil: "BR",
  mexico: "MX",
  japan: "JP",
};
function decoded(slug: string) {
  try {
    return decodeURIComponent(slug).toLowerCase();
  } catch {
    return slug.toLowerCase();
  }
}
export function cityPlace(slug: string): Place | null {
  const city = cities[decoded(slug)];
  return city ? { name: city[0], lat: city[1], lon: city[2] } : null;
}
// A market arrives as an ISO code (BR) or a Shazam country slug (united-kingdom).
export function marketPlace(market: string): Place | null {
  const code = slugs[decoded(market)] ?? market.toUpperCase();
  const place = markets[code];
  return place ? { name: place[0], lat: place[1], lon: place[2] } : null;
}
// A chart place's plain name: the city lookup first, then the decoded slug, then the country.
// City slugs arrive URL-encoded (s%C3%A3o-paulo).
export function placeName(place: {
  country: string | null;
  city: string | null;
}) {
  if (place.city)
    return cityPlace(place.city)?.name ?? countryName(decoded(place.city));
  if (!place.country) return "Global chart";
  return marketPlace(place.country)?.name ?? countryName(place.country);
}
const counted = (count: number, one: string, many: string) =>
  `${count.toLocaleString("en-US")} ${count === 1 ? one : many}`;
// One count line for a song's chart places over the last 28 days. A city chart counts as a city,
// a country chart as a country, and the global chart is named once, so the map and the names agree.
export function placeCounts(
  places: { country: string | null; city: string | null }[],
) {
  const cities = places.filter((place) => place.city).length;
  const countries = places.filter((place) => !place.city && place.country).length;
  const global = places.some((place) => !place.city && !place.country);
  if (!cities && !countries)
    return global
      ? "On the global Shazam chart in the last 28 days."
      : "No Shazam chart in the last 28 days.";
  const parts = [
    cities ? counted(cities, "city", "cities") : null,
    countries ? counted(countries, "country", "countries") : null,
  ].filter((part) => part !== null);
  return `On Shazam charts in ${parts.join(" and ")} in the last 28 days${global ? ", plus the global chart" : ""}.`;
}
export function placeList<T>(items: T[], find: (item: T) => Place | null) {
  return items.map(find).filter((place): place is Place => place !== null);
}
