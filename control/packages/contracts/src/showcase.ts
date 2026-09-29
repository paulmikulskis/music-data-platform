import { z } from "zod";
import { post } from "./shared.js";

export const showcaseContract = {
  link: post(
    "/showcase/link",
    z.object({
      person: z.string().regex(/^[a-z][a-z0-9_-]{0,47}$/),
      ttl_seconds: z.number().int().min(60).max(604800).default(86400),
    }),
    z.object({ link_url: z.url() }),
  ),
};
