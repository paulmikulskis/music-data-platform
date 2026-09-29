import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { AppError } from "./db.js";
const secret = randomBytes(32);
const previews = new Map<string, {actor:string; csv:string; target_set_id:string; expires:number}>();
const sign = (id:string) => createHmac("sha256",secret).update(id).digest("base64url");
export function savePreview(actor:string, csv:string, target_set_id:string) {
  for (const [key,value] of previews) if (value.expires < Date.now()) previews.delete(key);
  if (previews.size >= 100) previews.delete(previews.keys().next().value!);
  const id = randomBytes(24).toString("base64url");
  previews.set(id,{actor,csv,target_set_id,expires:Date.now()+30*60_000});
  return `${id}.${sign(id)}`;
}
export function readPreview(token:string, actor:string) {
  const [id='',signature=''] = token.split('.');
  const expected=sign(id), preview=previews.get(id);
  if (signature.length!==expected.length || !timingSafeEqual(Buffer.from(signature),Buffer.from(expected)) || !preview || preview.actor!==actor || preview.expires<Date.now())
    throw new AppError('preview_expired','This preview expired. Paste the CSV again to validate it.',422);
  return preview;
}
