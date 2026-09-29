import Image from "next/image";
export function Wordmark() {
  return (
    <Image
      className="wordmark"
      src="/mdp-wordmark.svg"
      alt="Music Data Platform"
      width={152}
      height={40}
      priority
    />
  );
}
